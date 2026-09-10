
"""
asp_utils2.py
Development interface for the first stages of the Pléiades ASP workflow.

Version 1.5.4
- conditional DIMAP tile validation with mandatory R1C1 anchor for merged products
- existing-project detection before accidental re-preparation
- clean new-project reset without any project-deletion control
- restore existing reference/pre-processing/post-processing results in the interface
- persistent project resume from project_settings.json and last-project pointer
- immediate clearing of stale run errors when a stage is retried
- optional co-registration notebook handoff after final DSM generation
- explicit image-tile merge caution and reusable prepared-data detection
- reliable embedded preliminary-DSM preview in the preprocessing tab
- responsive Run / Pause / Resume / Stop controls for long ASP tasks
- managed process-group termination so Stop cancels ASP child processes
- automatic tri-stereo time normalization: A=Forward, B=Middle, C=Backward
- restartable stage-by-stage ASP preprocessing
- organized per-stage preprocessing parameters
- metadata-integrated DIM vs RPC geometry comparison with parsed cam_test metrics
- bordered analysis tables and run-mode-aware advanced preprocessing controls
- selectable bundle-adjustment robust cost function
- renamed sections to user-friendly titles
- compact summaries + detailed logs saved to disk
- no external subprocess for metadata/geometry step
- added optional overview figure display for stereo / tri-stereo concept
- keeps the scientific logic of the original notebooks
"""
from __future__ import annotations

import html
import base64
import json
import os
import shutil
import subprocess
import sys
import traceback
import threading
import signal
import time
import warnings

try:
    import psutil
except Exception:  # optional fallback; core control still uses POSIX process groups
    psutil = None

from dataclasses import dataclass, asdict
from datetime import datetime
from itertools import combinations
from math import radians, degrees, sin, cos, acos, tan
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

import matplotlib.pyplot as plt
import matplotlib as mpl

mpl.rcParams["pdf.fonttype"] = 42
mpl.rcParams["ps.fonttype"] = 42
mpl.rcParams["text.usetex"] = False
import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.errors import NotGeoreferencedWarning
from rasterio.windows import Window

DEV_VERSION = "1.5.4"


RUNTIME_STAGE_DEFINITIONS = (
    (1, "prepare_data", "Prepare data"),
    (2, "metadata_geometry", "Metadata and geometry"),
    (3, "camera_comparison", "DIM vs RPC comparison"),
    (4, "reference_dem", "Reference DEM preparation"),
    (5, "bundle_adjustment", "Bundle adjustment"),
    (6, "preliminary_stereo", "Preliminary stereo"),
    (7, "preliminary_dem", "Preliminary DSM"),
    (8, "lidar_alignment", "Reference alignment (pc_align)"),
    (9, "camera_transform", "Camera transform"),
    (10, "map_projection", "Map projection"),
    (11, "point_cloud", "Point-cloud reconstruction"),
    (12, "final_dsm", "Final DSM"),
)


def _format_runtime_seconds(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{seconds:.1f} s"
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:d} h {minutes:02d} min {secs:02d} s"
    return f"{minutes:d} min {secs:02d} s"


def _empty_runtime_summary() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "order": order,
            "stage_key": key,
            "stage": label,
            "last_runtime_seconds": np.nan,
            "last_runtime": "",
            "last_completed": "",
            "run_count": 0,
        }
        for order, key, label in RUNTIME_STAGE_DEFINITIONS
    ])


def _load_runtime_summary(csv_path: Path) -> pd.DataFrame:
    base = _empty_runtime_summary().set_index("stage_key")
    csv_path = Path(csv_path)
    if csv_path.is_file():
        try:
            old = pd.read_csv(csv_path).set_index("stage_key")
            for key in base.index.intersection(old.index):
                for column in (
                    "last_runtime_seconds", "last_runtime",
                    "last_completed", "run_count",
                ):
                    if column in old.columns:
                        base.loc[key, column] = old.loc[key, column]
        except Exception:
            pass
    return base.reset_index().sort_values("order").reset_index(drop=True)


def _record_runtime(settings, stage_key: str, elapsed_seconds: float) -> pd.DataFrame:
    csv_path = settings.metadata_dir / "runtime_summary.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    df = _load_runtime_summary(csv_path)
    mask = df["stage_key"] == str(stage_key)
    if not mask.any():
        return df
    previous = pd.to_numeric(df.loc[mask, "run_count"], errors="coerce").fillna(0).iloc[0]
    df.loc[mask, "last_runtime_seconds"] = round(float(elapsed_seconds), 3)
    df.loc[mask, "last_runtime"] = _format_runtime_seconds(elapsed_seconds)
    df.loc[mask, "last_completed"] = datetime.now().astimezone().isoformat(timespec="seconds")
    df.loc[mask, "run_count"] = int(previous) + 1
    df.to_csv(csv_path, index=False)
    return df


# ============================================================
# SETTINGS
# ============================================================

@dataclass
class ProjectSettings:
    project_name: str
    output_base: str
    platform: str
    acquisition_mode: str

    acquisition_A: str
    acquisition_B: str
    acquisition_C: str = ""

    merge_tiles: bool = False
    tile_ids: Tuple[str, ...] = ("R1C1",)

    crop_enabled: bool = False
    aoi_vector: str = ""
    rpc_height: float = 2500.0
    buffer_px: int = 500
    spacing_deg: float = 0.00005

    overwrite: bool = False

    @property
    def project_dir(self) -> Path:
        return Path(self.output_base).expanduser() / self.project_name

    @property
    def merged_dir(self) -> Path:
        return self.project_dir / "merged_tiles"

    @property
    def cropped_dir(self) -> Path:
        return self.merged_dir / "cropped_images"

    @property
    def figure_dir(self) -> Path:
        return self.project_dir / "Figure"

    @property
    def log_dir(self) -> Path:
        return self.project_dir / "logs"

    @property
    def metadata_dir(self) -> Path:
        return self.project_dir / "metadata"

    @property
    def asp_logs_dir(self) -> Path:
        return self.project_dir / "asp_logs"

    @property
    def asp_out_dir(self) -> Path:
        # Keep ASP products with the active prepared imagery.  Full-image
        # workflows (including exact DIM) always write to full_data/asp_out;
        # legacy RPC AOI-crop workflows write to cropped_data/asp_out.
        data_root = "cropped_data" if self.crop_enabled else "full_data"
        return self.project_dir / data_root / "asp_out"

    @property
    def image_names(self) -> List[str]:
        return ["A", "B", "C"] if self.acquisition_mode == "tri_stereo" else ["A", "B"]

    @property
    def acquisition_folders(self) -> Dict[str, Path]:
        result = {
            "A": Path(self.acquisition_A).expanduser(),
            "B": Path(self.acquisition_B).expanduser(),
        }
        if self.acquisition_mode == "tri_stereo":
            result["C"] = Path(self.acquisition_C).expanduser()
        return result


class WorkflowLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("w", encoding="utf-8")
        self.write(f"Created: {datetime.now().isoformat(timespec='seconds')}")
        self.write("=" * 80)

    def write(self, msg=""):
        self._stream.write(str(msg) + "\n")
        self._stream.flush()

    def close(self):
        if not self._stream.closed:
            self._stream.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


class WorkflowCancelled(RuntimeError):
    """Raised when the user stops an active workflow execution."""


class ManagedProcessController:
    """Thread-safe controller for long-running ASP/GDAL subprocesses.

    Scientific commands are launched in their own POSIX process session.  The
    controller therefore acts on the *real process group*, not only the Python
    background worker.  v1.2.6 also verifies/records every control action so a
    failed Pause/Resume/Stop request is visible in the notebook instead of being
    silently ignored.
    """

    def __init__(self, state_callback=None):
        self._lock = threading.RLock()
        self._cancel_event = threading.Event()
        self._resume_event = threading.Event()
        self._resume_event.set()
        self._process = None
        self._task_label = ""
        self._command_label = ""
        self._state = "idle"
        self._state_callback = state_callback
        self._last_control_message = "Ready."
        self._last_control_ok = True
        self._callback_error = ""
        self._terminator_thread = None

    @property
    def state(self):
        with self._lock:
            return self._state

    @property
    def task_label(self):
        with self._lock:
            return self._task_label

    @property
    def command_label(self):
        with self._lock:
            return self._command_label

    @property
    def cancelled(self):
        return self._cancel_event.is_set()

    @property
    def pause_supported(self):
        return os.name == "posix" and hasattr(signal, "SIGSTOP") and hasattr(signal, "SIGCONT")

    @property
    def has_active_process(self):
        with self._lock:
            return self._process is not None and self._process.poll() is None

    @property
    def active_pid(self):
        with self._lock:
            process = self._process
        return None if process is None or process.poll() is not None else int(process.pid)

    @property
    def active_pgid(self):
        pid = self.active_pid
        if pid is None or os.name != "posix":
            return None
        try:
            return int(os.getpgid(pid))
        except Exception:
            return None

    @property
    def last_control_message(self):
        with self._lock:
            return self._last_control_message

    @property
    def last_control_ok(self):
        with self._lock:
            return self._last_control_ok

    @property
    def callback_error(self):
        with self._lock:
            return self._callback_error

    def _set_control_result(self, ok, message):
        with self._lock:
            self._last_control_ok = bool(ok)
            self._last_control_message = str(message)

    def _notify(self):
        callback = self._state_callback
        if callback is not None:
            try:
                callback(self)
                with self._lock:
                    self._callback_error = ""
            except Exception as exc:
                # Never hide a broken widget-state callback.  Keep the workflow
                # alive, but expose the failure through the controller state.
                with self._lock:
                    self._callback_error = f"{type(exc).__name__}: {exc}"

    def begin(self, task_label):
        with self._lock:
            if self._state not in {"idle", "finished", "stopped", "failed"}:
                raise RuntimeError(
                    f"Another workflow task is already active: {self._task_label or self._state}."
                )
            self._cancel_event.clear()
            self._resume_event.set()
            self._process = None
            self._task_label = str(task_label)
            self._command_label = ""
            self._state = "running"
            self._last_control_ok = True
            self._last_control_message = "Run started; waiting for the first external command."
        self._notify()

    def finish(self, state="finished"):
        with self._lock:
            self._process = None
            self._command_label = ""
            self._state = state
            self._resume_event.set()
            if state == "finished":
                self._last_control_ok = True
                self._last_control_message = "Run finished."
            elif state == "stopped":
                self._last_control_ok = True
                self._last_control_message = "Run stopped by user."
            elif state == "failed":
                self._last_control_ok = False
                self._last_control_message = "Run failed; see the workflow error/log."
        self._notify()
        with self._lock:
            self._state = "idle"
            self._task_label = ""
        self._notify()

    def check_cancelled(self):
        if self._cancel_event.is_set():
            raise WorkflowCancelled("Run stopped by user.")

    def wait_if_paused(self):
        while not self._resume_event.wait(timeout=0.2):
            self.check_cancelled()
        self.check_cancelled()

    def attach_process(self, process, command_label=""):
        with self._lock:
            self._process = process
            self._command_label = str(command_label)
            terminate_now = self._cancel_event.is_set()
            paused = not self._resume_event.is_set()
            pid = int(process.pid)
        if terminate_now:
            self._terminate_process_tree(process)
        elif paused and self.pause_supported:
            ok, msg = self._signal_process_tree(process, signal.SIGSTOP, "Pause")
            self._set_control_result(ok, msg)
        else:
            pgid = self._safe_pgid(process)
            extra = f", PGID {pgid}" if pgid is not None else ""
            self._set_control_result(True, f"Active command attached: PID {pid}{extra}.")
        self._notify()

    def detach_process(self, process):
        with self._lock:
            if self._process is process:
                self._process = None
                self._command_label = ""
                if not self._cancel_event.is_set() and self._state == "running":
                    self._last_control_ok = True
                    self._last_control_message = "Command finished; preparing the next workflow step."
        self._notify()

    def pause(self):
        with self._lock:
            if self._state == "paused":
                self._set_control_result(True, "Run is already paused.")
                self._notify()
                return True
            if self._state != "running":
                self._set_control_result(False, f"Pause ignored because controller state is '{self._state}'.")
                self._notify()
                return False
            if not self.pause_supported:
                self._set_control_result(False, "Pause/Resume is unavailable in this non-POSIX Python environment.")
                self._notify()
                return False
            self._resume_event.clear()
            process = self._process

        if process is not None and process.poll() is None:
            ok, msg = self._signal_process_tree(process, signal.SIGSTOP, "Pause")
            if not ok:
                self._resume_event.set()
                with self._lock:
                    self._state = "running"
                self._set_control_result(False, msg)
                self._notify()
                return False
        else:
            ok, msg = True, "Paused between external commands; the next ASP command will wait until Resume."

        with self._lock:
            self._state = "paused"
        self._set_control_result(ok, msg)
        self._notify()
        return True

    def resume(self):
        with self._lock:
            if self._state != "paused":
                self._set_control_result(False, f"Resume ignored because controller state is '{self._state}'.")
                self._notify()
                return False
            process = self._process

        if process is not None and process.poll() is None:
            ok, msg = self._signal_process_tree(process, signal.SIGCONT, "Resume")
            if not ok:
                self._set_control_result(False, msg)
                self._notify()
                return False
        else:
            ok, msg = True, "Resume accepted; workflow will continue with the next command."

        self._resume_event.set()
        with self._lock:
            self._state = "running"
        self._set_control_result(ok, msg)
        self._notify()
        return True

    def stop(self):
        with self._lock:
            if self._state == "idle":
                self._set_control_result(False, "Stop ignored because no workflow task is active.")
                self._notify()
                return False
            if self._state == "stopping":
                self._set_control_result(True, "Stop has already been requested; waiting for process termination.")
                self._notify()
                return True
            self._cancel_event.set()
            self._resume_event.set()
            process = self._process
            self._state = "stopping"
            pid = None if process is None else process.pid
            self._last_control_ok = True
            self._last_control_message = (
                f"Stop requested for PID {pid}; terminating the ASP process tree."
                if pid is not None
                else "Stop requested; cancellation will be applied at the next workflow checkpoint."
            )
        self._notify()

        if process is not None and process.poll() is None:
            # Do not block the ipywidget callback for the TERM/KILL grace period.
            def terminate_worker():
                ok, msg = self._terminate_process_tree(process, return_message=True)
                self._set_control_result(ok, msg)
                self._notify()

            thread = threading.Thread(
                target=terminate_worker,
                name="pleiades-asp-stop",
                daemon=True,
            )
            with self._lock:
                self._terminator_thread = thread
            thread.start()
        return True

    @staticmethod
    def _safe_pgid(process):
        if os.name != "posix" or process is None:
            return None
        try:
            return int(os.getpgid(process.pid))
        except Exception:
            return None

    @staticmethod
    def _descendant_processes(pid):
        if psutil is None:
            return []
        try:
            return psutil.Process(pid).children(recursive=True)
        except Exception:
            return []

    @classmethod
    def _signal_process_tree(cls, process, sig, action_name="Signal"):
        if process is None or process.poll() is not None:
            return False, f"{action_name} failed: the active process has already exited."

        errors = []
        delivered = False
        pgid = None

        if os.name == "posix":
            try:
                pgid = os.getpgid(process.pid)
                os.killpg(pgid, sig)
                delivered = True
            except ProcessLookupError:
                return False, f"{action_name} failed: process group no longer exists."
            except Exception as exc:
                errors.append(f"killpg: {type(exc).__name__}: {exc}")

        # psutil is a secondary path and also reaches descendants that elected
        # to create a different process group/session.
        if psutil is not None:
            try:
                parent = psutil.Process(process.pid)
                targets = parent.children(recursive=True) + [parent]
                method = "suspend" if sig == getattr(signal, "SIGSTOP", None) else "resume"
                if sig not in {getattr(signal, "SIGSTOP", None), getattr(signal, "SIGCONT", None)}:
                    method = None
                if method:
                    for proc in reversed(targets):
                        try:
                            getattr(proc, method)()
                            delivered = True
                        except (psutil.NoSuchProcess, psutil.ZombieProcess):
                            pass
                        except Exception as exc:
                            errors.append(f"PID {proc.pid}: {type(exc).__name__}: {exc}")
            except Exception as exc:
                errors.append(f"psutil: {type(exc).__name__}: {exc}")

        if not delivered:
            try:
                process.send_signal(sig)
                delivered = True
            except Exception as exc:
                errors.append(f"send_signal: {type(exc).__name__}: {exc}")

        if delivered:
            target = f"PGID {pgid}" if pgid is not None else f"PID {process.pid}"
            suffix = f" Warnings: {'; '.join(errors)}" if errors else ""
            return True, f"{action_name} signal delivered to {target}.{suffix}"
        return False, f"{action_name} failed. " + ("; ".join(errors) if errors else "No signal path succeeded.")

    @classmethod
    def _terminate_process_tree(cls, process, grace_seconds=2.0, return_message=False):
        if process is None or process.poll() is not None:
            result = (True, "Process had already exited before Stop was applied.")
            return result if return_message else None

        pid = process.pid
        pgid = cls._safe_pgid(process)
        errors = []

        # A stopped POSIX process should be continued before TERM so it can
        # perform normal signal handling/cleanup.
        if os.name == "posix" and hasattr(signal, "SIGCONT"):
            try:
                if pgid is not None:
                    os.killpg(pgid, signal.SIGCONT)
            except Exception:
                pass

        descendants = cls._descendant_processes(pid)

        if os.name == "posix":
            try:
                if pgid is not None:
                    os.killpg(pgid, signal.SIGTERM)
                else:
                    process.terminate()
            except ProcessLookupError:
                result = (True, f"Process group for PID {pid} had already exited.")
                return result if return_message else None
            except Exception as exc:
                errors.append(f"SIGTERM group: {type(exc).__name__}: {exc}")
                try:
                    process.terminate()
                except Exception as exc2:
                    errors.append(f"terminate PID: {type(exc2).__name__}: {exc2}")
        else:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=5,
                )
            except Exception as exc:
                errors.append(f"taskkill: {type(exc).__name__}: {exc}")
                try:
                    process.terminate()
                except Exception as exc2:
                    errors.append(f"terminate PID: {type(exc2).__name__}: {exc2}")

        # Explicitly terminate descendants too. This matters for launchers such
        # as parallel_stereo that may put workers in separate groups.
        if psutil is not None:
            for child in descendants:
                try:
                    child.terminate()
                except (psutil.NoSuchProcess, psutil.ZombieProcess):
                    pass
                except Exception as exc:
                    errors.append(f"terminate child {child.pid}: {type(exc).__name__}: {exc}")

        deadline = time.time() + float(grace_seconds)
        while process.poll() is None and time.time() < deadline:
            time.sleep(0.05)

        if process.poll() is None:
            if os.name == "posix":
                try:
                    if pgid is not None:
                        os.killpg(pgid, signal.SIGKILL)
                    else:
                        process.kill()
                except Exception as exc:
                    errors.append(f"SIGKILL: {type(exc).__name__}: {exc}")
            else:
                try:
                    process.kill()
                except Exception as exc:
                    errors.append(f"kill PID: {type(exc).__name__}: {exc}")

        if psutil is not None:
            for child in descendants:
                try:
                    if child.is_running():
                        child.kill()
                except (psutil.NoSuchProcess, psutil.ZombieProcess):
                    pass
                except Exception as exc:
                    errors.append(f"kill child {child.pid}: {type(exc).__name__}: {exc}")

        # Give the parent a short final window to reap.
        final_deadline = time.time() + 1.0
        while process.poll() is None and time.time() < final_deadline:
            time.sleep(0.05)

        ok = process.poll() is not None
        target = f"PID {pid}" + (f" / PGID {pgid}" if pgid is not None else "")
        if ok:
            msg = f"Stop delivered successfully; {target} exited."
            if errors:
                msg += " Warnings: " + "; ".join(errors)
        else:
            msg = f"Stop could not verify termination of {target}."
            if errors:
                msg += " Errors: " + "; ".join(errors)
        result = (ok, msg)
        return result if return_message else None


_EXECUTION_CONTEXT = threading.local()


def _current_process_controller():
    return getattr(_EXECUTION_CONTEXT, "controller", None)


def _set_current_process_controller(controller):
    _EXECUTION_CONTEXT.controller = controller


def _clear_current_process_controller():
    if hasattr(_EXECUTION_CONTEXT, "controller"):
        delattr(_EXECUTION_CONTEXT, "controller")


def _popen_kwargs_for_managed_process():
    if os.name == "posix":
        return {"start_new_session": True}
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return {"creationflags": creationflags} if creationflags else {}


# ============================================================
# FOLDERS / CONFIG
# ============================================================

def create_project_folders(settings: ProjectSettings) -> Dict[str, Path]:
    folders = {
        "project": settings.project_dir,
        "merged_tiles": settings.merged_dir,
        "cropped_images": settings.cropped_dir,
        "figures": settings.figure_dir,
        "logs": settings.log_dir,
        "metadata": settings.metadata_dir,
        "asp_logs": settings.asp_logs_dir,
        "asp_out": settings.asp_out_dir,
    }
    for folder in folders.values():
        folder.mkdir(parents=True, exist_ok=True)
    return folders


def save_project_config(settings: ProjectSettings) -> Path:
    config_path = settings.project_dir / "project_settings.json"
    payload = asdict(settings)
    payload["tile_ids"] = list(settings.tile_ids)
    payload["derived_paths"] = {
        "project_dir": str(settings.project_dir),
        "merged_tiles": str(settings.merged_dir),
        "cropped_images": str(settings.cropped_dir),
        "figure_dir": str(settings.figure_dir),
        "log_dir": str(settings.log_dir),
        "metadata_dir": str(settings.metadata_dir),
        "asp_logs": str(settings.asp_logs_dir),
        "asp_out": str(settings.asp_out_dir),
    }
    with config_path.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2)
    return config_path


def load_project_config(project_dir: str | Path) -> ProjectSettings:
    """Load a previously saved project without rerunning Prepare data."""
    project_dir = Path(project_dir).expanduser().resolve()
    config_path = project_dir / "project_settings.json"
    if not config_path.is_file():
        raise FileNotFoundError(
            f"Existing project configuration not found:\n{config_path}"
        )

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    field_names = set(ProjectSettings.__dataclass_fields__)
    kwargs = {key: value for key, value in payload.items() if key in field_names}
    if "tile_ids" in kwargs:
        kwargs["tile_ids"] = tuple(kwargs["tile_ids"] or ["R1C1"])

    settings = ProjectSettings(**kwargs)
    if settings.project_dir.resolve() != project_dir:
        # The project may have been moved after it was first created. Preserve
        # the project folder selected by the user while keeping all source-data
        # locations stored in project_settings.json.
        settings.output_base = str(project_dir.parent)
        settings.project_name = project_dir.name
    return settings


def inspect_existing_project(settings: ProjectSettings) -> Dict[str, object]:
    """Inspect reusable outputs from a previous notebook/session.

    This is intentionally diagnostic only: it never deletes, overwrites, or
    regenerates a product. The interface uses it to tell the user which stages
    can be resumed directly from disk.
    """
    prepared = {}
    missing = []
    for view in settings.image_names:
        image = settings.merged_dir / f"{view}.tif"
        rpc = _prepared_rpc_path(settings.merged_dir, view)
        dim = settings.merged_dir / f"DIM_{view}.XML"
        prepared[view] = {"image": image, "rpc": rpc, "dim": dim}
        if not image.is_file():
            missing.append(str(image))
        if not rpc.is_file():
            missing.append(str(rpc))

    metadata_files = [
        settings.metadata_dir / f"{settings.project_name}_overlap_pairs.csv",
        settings.metadata_dir / f"{settings.project_name}_image_metadata.csv",
        settings.metadata_dir / f"{settings.project_name}_stereo_geometry.csv",
    ]
    if settings.acquisition_mode == "tri_stereo":
        metadata_files.append(
            settings.metadata_dir / f"{settings.project_name}_view_assignment.csv"
        )

    metadata_ready = all(path.is_file() for path in metadata_files)
    processing_state = settings.project_dir / "processing_state.json"
    reference_config = settings.project_dir / "reference_dems" / "reference_dem_config.json"
    final_products = settings.metadata_dir / "final_dsm_products.csv"

    return {
        "prepared_ready": not missing,
        "prepared": prepared,
        "missing_prepared": missing,
        "metadata_ready": metadata_ready,
        "metadata_files": metadata_files,
        "processing_state": processing_state,
        "reference_config": reference_config,
        "final_products": final_products,
        "final_dsm_ready": final_products.is_file(),
    }


# ============================================================
# PREPARE DATA
# ============================================================

IMAGE_EXTENSIONS = ("TIF", "tif", "TIFF", "tiff", "JP2", "jp2")


def _first(paths):
    paths = sorted(paths)
    return paths[0] if paths else None


def _find_rpc_file(folder: Path, platform: str) -> Path:
    rpc = _first(list(folder.glob(f"RPC_{platform}*.XML")) + list(folder.glob(f"RPC_{platform}*.xml")))
    if rpc is None:
        raise FileNotFoundError(f"No RPC file found in:\n{folder}\nExpected pattern: RPC_{platform}*.XML")
    return rpc


def _find_dim_file(folder: Path, platform: str) -> Optional[Path]:
    return _first(list(folder.glob(f"DIM_{platform}*.XML")) + list(folder.glob(f"DIM_{platform}*.xml")))


def _find_tile_files(folder: Path, tile_ids: Sequence[str], log: WorkflowLog) -> List[Path]:
    selected = []
    for tile in tile_ids:
        match = None
        for ext in IMAGE_EXTENSIONS:
            candidates = sorted(folder.glob(f"*{tile}.{ext}"))
            if candidates:
                match = candidates[0]
                break
        if match is None:
            log.write(f"WARNING: no image found for tile {tile}")
        else:
            selected.append(match)
    if not selected:
        raise FileNotFoundError(f"No selected image tiles found in:\n{folder}\nRequested: {', '.join(tile_ids)}")
    return selected


def validate_project_inputs(settings: ProjectSettings, log: WorkflowLog) -> Dict[str, Dict[str, object]]:
    if not settings.project_name.strip():
        raise ValueError("Project name cannot be empty.")
    if not settings.output_base.strip():
        raise ValueError("Output base folder cannot be empty.")
    if not settings.platform.strip():
        raise ValueError("Platform cannot be empty.")
    if not settings.tile_ids:
        raise ValueError("At least one Tile ID is required.")

    normalized_tiles = tuple(str(tile).strip().upper() for tile in settings.tile_ids if str(tile).strip())
    has_r1c1 = "R1C1" in normalized_tiles

    if settings.merge_tiles:
        if not has_r1c1:
            raise ValueError(
                "Merge selected image tiles is ON, but R1C1 is missing. "
                "For one tiled DIMAP image product, include R1C1 together with every additional tile required by the image (for example R1C1,R1C2)."
            )
    else:
        if len(normalized_tiles) != 1:
            raise ValueError(
                "Merge selected image tiles is OFF. Use only R1C1, or enable merging and include R1C1 together with the additional tile IDs."
            )
        if normalized_tiles[0] != "R1C1":
            raise ValueError(
                f"Tile {normalized_tiles[0]} was selected without R1C1. "
                "For a tiled DIMAP image product, use R1C1 alone when no merge is needed, or enable merging and include R1C1 together with the additional tile IDs."
            )
    if settings.crop_enabled:
        if not settings.aoi_vector.strip():
            raise ValueError("Crop is ON, but no AOI vector was selected.")
        if not Path(settings.aoi_vector).expanduser().is_file():
            raise FileNotFoundError(f"AOI vector does not exist:\n{settings.aoi_vector}")

    inspected = {}
    for name, folder in settings.acquisition_folders.items():
        folder = Path(folder)
        if not folder.is_dir():
            raise FileNotFoundError(
                f"Acquisition {name} folder does not exist:\n{folder}\n\nSelect the IMG_* folder containing image tile(s), RPC XML, and DIM XML."
            )
        rpc = _find_rpc_file(folder, settings.platform)
        dim = _find_dim_file(folder, settings.platform)
        tiles = _find_tile_files(folder, settings.tile_ids, log)
        log.write(f"Acquisition {name}: {folder}")
        log.write(f"  RPC: {rpc}")
        log.write(f"  DIM: {dim if dim else 'not found'}")
        for tile in tiles:
            log.write(f"  image: {tile}")
        inspected[name] = {"folder": folder, "rpc": rpc, "dim": dim, "tiles": tiles}
    return inspected


def _clean_gdal_env():
    env = os.environ.copy()
    env.pop("GDAL_DRIVER_PATH", None)
    env.pop("LD_LIBRARY_PATH", None)
    return env


def _find_cmd(command: str) -> Optional[str]:
    fixed = Path("/usr/bin") / command
    if fixed.is_file():
        return str(fixed)
    return shutil.which(command)


def _remove_output(path: Path, overwrite: bool):
    if not path.exists():
        return
    if not overwrite:
        raise FileExistsError(f"Output already exists:\n{path}\nEnable overwrite only if you want to replace it.")
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _prepare_single_tile(source: Path, destination: Path, overwrite: bool, log: WorkflowLog):
    _remove_output(destination, overwrite)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() in {".tif", ".tiff"}:
        shutil.copy2(source, destination)
        return
    if source.suffix.lower() == ".jp2":
        gdal_translate = _find_cmd("gdal_translate")
        if gdal_translate:
            subprocess.run([gdal_translate, str(source), str(destination), "-co", "TILED=YES"], check=True, env=_clean_gdal_env(), stdout=log._stream, stderr=subprocess.STDOUT)
            return
        with rasterio.open(source) as src:
            profile = src.profile.copy()
            profile.update(driver="GTiff", tiled=True)
            with rasterio.open(destination, "w", **profile) as dst:
                for band in range(1, src.count + 1):
                    dst.write(src.read(band), band)
            return
    raise ValueError(f"Unsupported image extension: {source.suffix}")


def _merge_tiles(tile_files: Sequence[Path], out_tif: Path, out_vrt: Path, overwrite: bool, log: WorkflowLog):
    _remove_output(out_tif, overwrite)
    if out_vrt.exists():
        out_vrt.unlink()
    gdalbuildvrt = _find_cmd("gdalbuildvrt")
    gdal_translate = _find_cmd("gdal_translate")
    if not gdalbuildvrt or not gdal_translate:
        raise RuntimeError("Multi-tile preparation requires gdalbuildvrt and gdal_translate.")
    subprocess.run([gdalbuildvrt, str(out_vrt), *[str(p) for p in tile_files]], check=True, env=_clean_gdal_env(), stdout=log._stream, stderr=subprocess.STDOUT)
    subprocess.run([gdal_translate, str(out_vrt), str(out_tif), "-co", "TILED=YES"], check=True, env=_clean_gdal_env(), stdout=log._stream, stderr=subprocess.STDOUT)


def prepare_one_acquisition(name: str, inspected: Dict[str, object], settings: ProjectSettings, log: WorkflowLog):
    tile_files = list(inspected["tiles"])
    rpc_file = Path(inspected["rpc"])
    dim_file = inspected["dim"]
    out_tif = settings.merged_dir / f"{name}.tif"
    out_vrt = settings.merged_dir / f"{name}.vrt"
    out_rpc = settings.merged_dir / f"RPC_{name}.XML"
    out_dim = settings.merged_dir / f"DIM_{name}.XML"
    log.write(f"Preparing acquisition {name}")
    if settings.merge_tiles and len(tile_files) > 1:
        _merge_tiles(tile_files, out_tif, out_vrt, settings.overwrite, log)
    else:
        _prepare_single_tile(tile_files[0], out_tif, settings.overwrite, log)
    _remove_output(out_rpc, settings.overwrite)
    shutil.copy2(rpc_file, out_rpc)
    if dim_file is not None:
        _remove_output(out_dim, settings.overwrite)
        shutil.copy2(Path(dim_file), out_dim)
    return {"image": out_tif, "rpc": out_rpc, "dim": out_dim if dim_file is not None else None}


# ============================================================
# OPTIONAL AOI CROP
# ============================================================

def _require_rpcm():
    try:
        from rpcm.rpc_model import RPCModel
        return RPCModel
    except ImportError as exc:
        raise ImportError("AOI cropping requires rpcm RPCModel, which is a standard workflow dependency and should be installed automatically. Reinstall or update pleiades-asp-workflow in the active Python environment.") from exc


def load_rpc_from_xml(xml_path):
    RPCModel = _require_rpcm()
    tree = ET.parse(xml_path)
    root = tree.getroot()
    rfm = root.find(".//Rational_Function_Model/Global_RFM")
    if rfm is None:
        raise ValueError(f"Cannot find Rational_Function_Model/Global_RFM in:\n{xml_path}")
    def get(tag):
        elem = rfm.find(f".//{tag}")
        if elem is None:
            raise ValueError(f"Missing RPC tag {tag} in:\n{xml_path}")
        return elem.text.strip()
    def coeffs(prefix):
        return " ".join(get(f"{prefix}_{i}") for i in range(1, 21))
    rpc_dict = {
        "LINE_OFF": get("LINE_OFF"),
        "SAMP_OFF": get("SAMP_OFF"),
        "LAT_OFF": get("LAT_OFF"),
        "LONG_OFF": get("LONG_OFF"),
        "HEIGHT_OFF": get("HEIGHT_OFF"),
        "LINE_SCALE": get("LINE_SCALE"),
        "SAMP_SCALE": get("SAMP_SCALE"),
        "LAT_SCALE": get("LAT_SCALE"),
        "LONG_SCALE": get("LONG_SCALE"),
        "HEIGHT_SCALE": get("HEIGHT_SCALE"),
        "LINE_NUM_COEFF": coeffs("LINE_NUM_COEFF"),
        "LINE_DEN_COEFF": coeffs("LINE_DEN_COEFF"),
        "SAMP_NUM_COEFF": coeffs("SAMP_NUM_COEFF"),
        "SAMP_DEN_COEFF": coeffs("SAMP_DEN_COEFF"),
    }
    return RPCModel(rpc_dict, dict_format="geotiff")


def update_rpc_offsets(in_xml, out_xml, col0, row0):
    tree = ET.parse(in_xml)
    root = tree.getroot()
    samp = root.find(".//SAMP_OFF")
    line = root.find(".//LINE_OFF")
    if samp is None or line is None:
        raise ValueError(f"Missing LINE_OFF/SAMP_OFF in:\n{in_xml}")
    samp.text = str(float(samp.text) - col0)
    line.text = str(float(line.text) - row0)
    out_xml = Path(out_xml)
    out_xml.parent.mkdir(parents=True, exist_ok=True)
    tree.write(out_xml, encoding="UTF-8", xml_declaration=True)


def densify_linestring(line, spacing_deg):
    n_points = max(2, int(line.length / spacing_deg))
    return [line.interpolate(i / (n_points - 1), normalized=True).coords[0] for i in range(n_points)]


def extract_aoi_lonlat_points(vector_path, spacing_deg):
    import geopandas as gpd
    gdf = gpd.read_file(vector_path)
    if gdf.empty:
        raise ValueError(f"AOI vector is empty:\n{vector_path}")
    if gdf.crs is None:
        raise ValueError("AOI vector has no CRS.")
    gdf = gdf.to_crs("EPSG:4326")
    try:
        geom = gdf.geometry.union_all()
    except AttributeError:
        geom = gdf.geometry.unary_union
    points = []
    def add_geom(g):
        if g.geom_type == "Polygon":
            points.extend(densify_linestring(g.exterior, spacing_deg))
        elif g.geom_type == "MultiPolygon":
            for part in g.geoms:
                points.extend(densify_linestring(part.exterior, spacing_deg))
        elif g.geom_type == "LineString":
            points.extend(densify_linestring(g, spacing_deg))
        elif g.geom_type == "MultiLineString":
            for part in g.geoms:
                points.extend(densify_linestring(part, spacing_deg))
        else:
            raise ValueError(f"Unsupported AOI geometry type: {g.geom_type}")
    add_geom(geom)
    if not points:
        raise ValueError("No AOI points were extracted.")
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    return lons, lats


def get_rpc_window(rpc_path, lons, lats, height, buffer_px):
    rpc = load_rpc_from_xml(rpc_path)
    heights = [height] * len(lons)
    cols, rows = rpc.projection(lons, lats, heights)
    col_min = int(np.floor(np.min(cols))) - buffer_px
    col_max = int(np.ceil(np.max(cols))) + buffer_px
    row_min = int(np.floor(np.min(rows))) - buffer_px
    row_max = int(np.ceil(np.max(rows))) + buffer_px
    return col_min, col_max, row_min, row_max


def crop_one_rpc_image(img_path, rpc_path, out_img_path, out_rpc_path, rpc_window, overwrite):
    _remove_output(Path(out_img_path), overwrite)
    _remove_output(Path(out_rpc_path), overwrite)
    col_min, col_max, row_min, row_max = rpc_window
    with rasterio.open(img_path) as src:
        col0 = max(0, col_min)
        row0 = max(0, row_min)
        col1 = min(src.width, col_max)
        row1 = min(src.height, row_max)
        width = col1 - col0
        height_crop = row1 - row0
        if width <= 0 or height_crop <= 0:
            raise ValueError(f"No overlap between AOI and image:\n{img_path}")
        window = Window(col0, row0, width, height_crop)
        data = src.read(window=window)
        transform = src.window_transform(window)
        meta = src.meta.copy()
        meta.update({"height": data.shape[1], "width": data.shape[2], "transform": transform})
    Path(out_img_path).parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_img_path, "w", **meta) as dst:
        dst.write(data)
    update_rpc_offsets(in_xml=rpc_path, out_xml=out_rpc_path, col0=col0, row0=row0)
    return col0, row0, width, height_crop


def crop_prepared_images(settings: ProjectSettings, log: WorkflowLog):
    lons, lats = extract_aoi_lonlat_points(settings.aoi_vector, settings.spacing_deg)
    results = {}
    for name in settings.image_names:
        img_path = settings.merged_dir / f"{name}.tif"
        rpc_path = _prepared_rpc_path(settings.merged_dir, name)
        out_name = f"{name}_crop"
        out_img = settings.cropped_dir / f"{out_name}.tif"
        out_rpc = settings.cropped_dir / f"RPC_{out_name}.XML"
        rpc_window = get_rpc_window(rpc_path, lons, lats, settings.rpc_height, settings.buffer_px)
        final_window = crop_one_rpc_image(img_path, rpc_path, out_img, out_rpc, rpc_window, settings.overwrite)
        src_dim = settings.merged_dir / f"DIM_{name}.XML"
        dst_dim = settings.cropped_dir / f"DIM_{out_name}.XML"
        if src_dim.exists():
            _remove_output(dst_dim, settings.overwrite)
            shutil.copy2(src_dim, dst_dim)
        else:
            dst_dim = None
        log.write(f"Cropped {name}: {out_img}")
        results[name] = {"image": out_img, "rpc": out_rpc, "dim": dst_dim, "window": final_window}
    return results


def _read_preview(path: Path, max_display_size=1200, percentiles=(2, 98)):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with rasterio.open(path) as src:
            scale = max(src.width / max_display_size, src.height / max_display_size, 1.0)
            out_width = max(1, int(round(src.width / scale)))
            out_height = max(1, int(round(src.height / scale)))
            image = src.read(1, out_shape=(out_height, out_width), resampling=Resampling.bilinear).astype("float32")
            try:
                mask = src.dataset_mask(out_shape=(out_height, out_width), resampling=Resampling.nearest) == 0
            except TypeError:
                mask = src.dataset_mask() == 0
                if mask.shape != image.shape:
                    mask = np.zeros_like(image, dtype=bool)
            if src.nodata is not None:
                mask |= np.isclose(image, src.nodata)
            mask |= np.isclose(image, 0)
    valid = (~mask) & np.isfinite(image)
    if valid.any():
        low, high = np.nanpercentile(image[valid], percentiles)
        if high <= low:
            high = low + 1.0
        stretched = np.clip((image - low) / (high - low), 0, 1)
    else:
        stretched = np.zeros_like(image, dtype="float32")
    return np.ma.array(stretched, mask=mask)


def make_preview(settings: ProjectSettings, cropped: bool):
    stage = "cropped" if cropped else "prepared"
    paths = [settings.cropped_dir / f"{name}_crop.tif" for name in settings.image_names] if cropped else [settings.merged_dir / f"{name}.tif" for name in settings.image_names]
    arrays = [_read_preview(path) for path in paths]
    fig, axes = plt.subplots(1, len(arrays), figsize=(5.0 * len(arrays), 5.5), squeeze=False)
    axes = axes.ravel()
    cmap = plt.get_cmap("gray").copy()
    cmap.set_bad("white")
    view_labels = _workflow_view_display_labels(settings)
    for ax, name, array in zip(axes, settings.image_names, arrays):
        ax.imshow(array, cmap=cmap)
        ax.set_title(view_labels.get(name, f"Image {name}"))
        ax.set_xlabel("Column")
        ax.set_ylabel("Row")
        ax.grid(True, alpha=0.22, linewidth=0.5, linestyle="--")
    stage_title = "cropped" if cropped else "prepared"
    fig.suptitle(f"{settings.project_name} — {stage_title} image preview")
    fig.tight_layout()
    settings.figure_dir.mkdir(parents=True, exist_ok=True)
    png = settings.figure_dir / f"{stage}_images_preview.png"
    pdf = settings.figure_dir / f"{stage}_images_preview.pdf"
    fig.savefig(png, dpi=200, bbox_inches="tight")
    fig.savefig(pdf, format="pdf", bbox_inches="tight")
    return {"figure": fig, "png": png, "pdf": pdf, "images": paths, "stage": stage}


def run_prepare_data(settings: ProjectSettings, progress_callback=None):
    create_project_folders(settings)
    log_path = settings.log_dir / "prepare_data.log"
    with WorkflowLog(log_path) as log:
        try:
            if progress_callback: progress_callback(5, "Validating inputs")
            inspected = validate_project_inputs(settings, log)
            if progress_callback: progress_callback(10, "Saving project configuration")
            config_path = save_project_config(settings)
            preparation = {}
            names = settings.image_names
            for index, name in enumerate(names, start=1):
                if progress_callback:
                    value = 20 + int((index - 1) / len(names) * 35)
                    progress_callback(value, f"Preparing acquisition {name}")
                preparation[name] = prepare_one_acquisition(name, inspected[name], settings, log)
            crop = {}
            if settings.crop_enabled:
                if progress_callback: progress_callback(65, "Cropping prepared images")
                crop = crop_prepared_images(settings, log)
                preview_is_cropped = True
            else:
                preview_is_cropped = False
            if progress_callback: progress_callback(88, "Creating image preview")
            preview = make_preview(settings, cropped=preview_is_cropped)
            active_images = {name: settings.cropped_dir / f"{name}_crop.tif" for name in settings.image_names} if preview_is_cropped else {name: settings.merged_dir / f"{name}.tif" for name in settings.image_names}
            if progress_callback: progress_callback(100, "Completed")
            log.write(f"Active image stage: {preview['stage']}")
            for name, path in active_images.items():
                log.write(f"Active image {name}: {path}")
            return {"config_path": config_path, "preparation": preparation, "crop": crop, "active_images": active_images, "preview": preview, "log_path": log_path}
        except Exception:
            log.write(traceback.format_exc())
            raise


# ============================================================
# METADATA AND GEOMETRY
# Internal implementation adapted from user's script.
# ============================================================

def clean_tag(tag):
    return tag.split("}")[-1]


def safe_float(value):
    if value is None:
        return None
    try:
        return float(str(value).replace(",", ".").strip())
    except Exception:
        return None


def _prepared_rpc_path(img_dir, image_id):
    """Return the prepared RPC XML, preferring the unambiguous RPC_* name.

    v1.2.6 writes RPC_A.XML / RPC_B.XML / RPC_C.XML (and RPC_A_crop.XML
    for legacy cropped-RPC mode).  The fallback keeps existing projects made
    by <=1.2.3 readable without forcing users to re-prepare their data.
    """
    img_dir = Path(img_dir)
    preferred = img_dir / f"RPC_{image_id}.XML"
    legacy = img_dir / f"{image_id}.XML"
    if preferred.is_file():
        return preferred
    if legacy.is_file():
        return legacy
    return preferred


def find_image_ids(img_dir):
    image_ids = []
    for filename in sorted(os.listdir(img_dir)):
        if filename.lower().endswith(".tif"):
            image_id = os.path.splitext(filename)[0]
            tif_path = os.path.join(img_dir, f"{image_id}.tif")
            rpc_path = _prepared_rpc_path(img_dir, image_id)
            if os.path.isfile(tif_path) and os.path.isfile(rpc_path):
                image_ids.append(image_id)
    return image_ids


def parse_pairs_argument(pairs_arg):
    if not pairs_arg:
        return None
    pairs = []
    for item in pairs_arg:
        if ":" not in item:
            raise ValueError(f"Invalid pair format: {item}. Use LEFT:RIGHT, for example A:B.")
        left, right = item.split(":", 1)
        pairs.append((left.strip(), right.strip()))
    return pairs


def get_rpc_params(xml_path):
    tree = ET.parse(xml_path)
    root = tree.getroot()
    tags = ["SAMP_OFF", "SAMP_SCALE", "LINE_OFF", "LINE_SCALE", "LAT_OFF", "LAT_SCALE", "LONG_OFF", "LONG_SCALE"]
    params = {}
    for tag in tags:
        elem = root.find(f".//{tag}")
        params[tag] = float(elem.text) if elem is not None and elem.text is not None else (1.0 if tag.endswith("SCALE") else 0.0)
    return params


def approximate_footprint(img_path, rpc):
    from shapely.geometry import box
    with rasterio.open(img_path) as src:
        width, height = src.width, src.height
    x_min = -rpc["SAMP_OFF"] / rpc["SAMP_SCALE"]
    x_max = (width - rpc["SAMP_OFF"]) / rpc["SAMP_SCALE"]
    y_min = -rpc["LINE_OFF"] / rpc["LINE_SCALE"]
    y_max = (height - rpc["LINE_OFF"]) / rpc["LINE_SCALE"]
    lon_min = rpc["LONG_OFF"] + x_min * rpc["LONG_SCALE"]
    lon_max = rpc["LONG_OFF"] + x_max * rpc["LONG_SCALE"]
    lat_min = rpc["LAT_OFF"] + y_min * rpc["LAT_SCALE"]
    lat_max = rpc["LAT_OFF"] + y_max * rpc["LAT_SCALE"]
    return box(lon_min, lat_min, lon_max, lat_max)


def build_footprints(img_dir, image_ids):
    footprints = {}
    for image_id in image_ids:
        img_path = os.path.join(img_dir, f"{image_id}.tif")
        rpc_path = _prepared_rpc_path(img_dir, image_id)
        rpc = get_rpc_params(rpc_path)
        footprints[image_id] = approximate_footprint(img_path, rpc)
    return footprints


def compute_overlap_table(footprints, pairs, iou_thresh):
    records = []
    for left, right in pairs:
        if left not in footprints or right not in footprints:
            records.append({"pair": f"{left}{right}", "left_image": left, "right_image": right, "iou": None, "is_valid": False, "note": "missing footprint"})
            continue
        poly_left = footprints[left]
        poly_right = footprints[right]
        inter_area = poly_left.intersection(poly_right).area
        union_area = poly_left.union(poly_right).area
        iou = inter_area / union_area if union_area > 0 else 0.0
        records.append({"pair": f"{left}{right}", "left_image": left, "right_image": right, "iou": round(iou, 4), "is_valid": iou >= iou_thresh, "note": ""})
    return pd.DataFrame(records)


def extract_image_id_from_dim(dim_filename):
    name = os.path.basename(dim_filename)
    name = os.path.splitext(name)[0]
    return name.replace("DIM_", "", 1) if name.startswith("DIM_") else name


def parse_dim_xml(xml_path):
    try:
        tree = ET.parse(xml_path)
        return tree.getroot()
    except ET.ParseError:
        return None


def extract_dim_metadata(root, xml_path):
    image_id = extract_image_id_from_dim(xml_path)
    metadata = {"image_id": image_id, "dim_file": os.path.basename(xml_path), "tif_file": f"{image_id}.tif", "rpc_file": f"RPC_{image_id}.XML"}
    target_tags = {"NBANDS", "NBITS", "FOCAL_LENGTH", "AZIMUTH_ANGLE", "VIEWING_ANGLE_ACROSS_TRACK", "VIEWING_ANGLE_ALONG_TRACK", "VIEWING_ANGLE", "INCIDENCE_ANGLE_ALONG_TRACK", "INCIDENCE_ANGLE_ACROSS_TRACK", "INCIDENCE_ANGLE", "SUN_AZIMUTH", "SUN_ELEVATION", "IMAGING_DATE", "IMAGING_TIME"}
    for elem in root.iter():
        tag = clean_tag(elem.tag)
        text = elem.text.strip() if elem.text and elem.text.strip() else None
        if tag in target_tags:
            metadata[tag] = text
        if tag == "Planimetric_Accuracy":
            for sub_elem in elem:
                sub_tag = clean_tag(sub_elem.tag)
                sub_text = sub_elem.text.strip() if sub_elem.text and sub_elem.text.strip() else None
                if sub_tag in ["MEAN", "STDV", "CE90"]:
                    metadata[f"Planimetric_Accuracy_{sub_tag}"] = sub_text
    return metadata


def _metadata_acquisition_datetime(metadata_df: pd.DataFrame) -> pd.Series:
    """Return acquisition datetimes used to normalize tri-stereo view order."""
    if metadata_df.empty:
        return pd.Series(dtype="datetime64[ns]")

    if "IMAGING_DATE" not in metadata_df.columns or "IMAGING_TIME" not in metadata_df.columns:
        return pd.Series(pd.NaT, index=metadata_df.index, dtype="datetime64[ns]")

    text = (
        metadata_df["IMAGING_DATE"].fillna("").astype(str).str.strip()
        + " "
        + metadata_df["IMAGING_TIME"].fillna("").astype(str).str.strip()
    )

    # utc=True also accepts common DIMAP time strings ending in Z.  Convert
    # back to timezone-naive timestamps solely for deterministic sorting.
    parsed = pd.to_datetime(text, errors="coerce", utc=True)
    try:
        return parsed.dt.tz_convert(None)
    except Exception:
        return parsed


def build_image_metadata_table(img_dir, image_ids):
    records = []
    for image_id in image_ids:
        dim_path = os.path.join(img_dir, f"DIM_{image_id}.XML")
        if not os.path.isfile(dim_path):
            continue
        root = parse_dim_xml(dim_path)
        if root is None:
            continue
        records.append(extract_dim_metadata(root, dim_path))

    metadata_df = pd.DataFrame(records)
    if metadata_df.empty:
        return metadata_df

    acquisition_dt = _metadata_acquisition_datetime(metadata_df)
    if acquisition_dt.notna().any():
        metadata_df = metadata_df.assign(_acquisition_dt=acquisition_dt)
        metadata_df = metadata_df.sort_values(
            by=["_acquisition_dt", "image_id"], na_position="last"
        ).reset_index(drop=True)
        metadata_df = metadata_df.drop(columns=["_acquisition_dt"])
    elif "IMAGING_DATE" in metadata_df.columns and "IMAGING_TIME" in metadata_df.columns:
        metadata_df = metadata_df.sort_values(
            by=["IMAGING_DATE", "IMAGING_TIME", "image_id"]
        ).reset_index(drop=True)

    if len(metadata_df) == 3:
        metadata_df.insert(
            1,
            "view_order",
            [
                "Forward (F)",
                "Middle / near-nadir (M)",
                "Backward (B)",
            ],
        )
    elif len(metadata_df) == 2:
        metadata_df.insert(1, "view_order", ["Image_1", "Image_2"])
    else:
        metadata_df.insert(
            1,
            "view_order",
            [f"Image_{i+1}" for i in range(len(metadata_df))],
        )
    return metadata_df


TRI_STEREO_WORKFLOW_VIEWS = (
    ("A", "Forward", "F"),
    ("B", "Middle / near-nadir", "M"),
    ("C", "Backward", "B"),
)


def _workflow_view_display_labels(settings: ProjectSettings) -> Dict[str, str]:
    """Human-readable view titles aligned with the schematic acquisition figure."""
    if getattr(settings, "acquisition_mode", "tri_stereo") == "tri_stereo":
        return {
            "A": "Forward image (F)",
            "B": "Near-nadir (Middle) image (M)",
            "C": "Backward image (B)",
        }
    return {
        "A": "Forward image (F)",
        "B": "Backward image (B)",
    }


def _tri_stereo_time_assignment(metadata_df: pd.DataFrame) -> Tuple[Dict[str, str], pd.DataFrame]:
    """
    Map the three prepared input slots to the workflow's fixed A/B/C meaning.

    Chronological acquisition order is interpreted as:
        earliest -> A = Forward (F)
        middle   -> B = Middle / near-nadir (M)
        latest   -> C = Backward (B)

    The mapping is intentionally based on DIMAP acquisition time, not on the
    folder slot selected by the user.
    """
    if len(metadata_df) != 3 or "image_id" not in metadata_df.columns:
        raise ValueError(
            "Automatic Forward/Middle/Backward assignment requires exactly "
            "three prepared images with DIM metadata."
        )

    dt = _metadata_acquisition_datetime(metadata_df)
    if dt.isna().any():
        missing = metadata_df.loc[dt.isna(), "image_id"].astype(str).tolist()
        raise ValueError(
            "Cannot determine tri-stereo acquisition order because IMAGING_DATE/"
            "IMAGING_TIME is missing or invalid for: " + ", ".join(missing)
        )

    if dt.duplicated().any():
        raise ValueError(
            "Cannot determine a unique Forward/Middle/Backward order because "
            "two or more DIM files have the same acquisition timestamp."
        )

    ordered = (
        metadata_df.assign(_acquisition_dt=dt)
        .sort_values("_acquisition_dt")
        .reset_index(drop=True)
    )

    mapping = {}
    rows = []
    for index, (workflow_id, role, figure_symbol) in enumerate(TRI_STEREO_WORKFLOW_VIEWS):
        row = ordered.iloc[index]
        old_id = str(row["image_id"])
        mapping[old_id] = workflow_id
        rows.append(
            {
                "input_label_before_normalization": old_id,
                "assigned_workflow_id": workflow_id,
                "view_role": role,
                "figure_symbol": figure_symbol,
                "IMAGING_DATE": row.get("IMAGING_DATE"),
                "IMAGING_TIME": row.get("IMAGING_TIME"),
                "acquisition_datetime": row["_acquisition_dt"].isoformat(),
            }
        )

    return mapping, pd.DataFrame(rows)


def _view_asset_specs(settings: ProjectSettings, view: str):
    """All prepared files whose basename carries an A/B/C workflow view."""
    specs = [
        (settings.merged_dir / f"{view}.tif", settings.merged_dir, "{view}.tif"),
        (settings.merged_dir / f"RPC_{view}.XML", settings.merged_dir, "RPC_{view}.XML"),
        # Legacy <=1.2.3 prepared RPC naming; renamed only when it exists.
        (settings.merged_dir / f"{view}.XML", settings.merged_dir, "{view}.XML"),
        (settings.merged_dir / f"DIM_{view}.XML", settings.merged_dir, "DIM_{view}.XML"),
        (settings.merged_dir / f"{view}.vrt", settings.merged_dir, "{view}.vrt"),
    ]
    if settings.cropped_dir.exists():
        specs.extend(
            [
                (settings.cropped_dir / f"{view}_crop.tif", settings.cropped_dir, "{view}_crop.tif"),
                (settings.cropped_dir / f"RPC_{view}_crop.XML", settings.cropped_dir, "RPC_{view}_crop.XML"),
                # Legacy <=1.2.3 cropped RPC naming.
                (settings.cropped_dir / f"{view}_crop.XML", settings.cropped_dir, "{view}_crop.XML"),
                (settings.cropped_dir / f"DIM_{view}_crop.XML", settings.cropped_dir, "DIM_{view}_crop.XML"),
            ]
        )
    return specs


def _normalize_tri_stereo_prepared_files(
    settings: ProjectSettings,
    metadata_df: pd.DataFrame,
    log: WorkflowLog,
):
    """Atomically relabel prepared A/B/C products into time-normalized F/M/B order."""
    mapping, assignment_df = _tri_stereo_time_assignment(metadata_df)

    source_folders = settings.acquisition_folders
    assignment_df.insert(
        1,
        "source_acquisition_folder",
        assignment_df["input_label_before_normalization"].map(
            lambda old: str(source_folders.get(str(old), ""))
        ),
    )

    if set(mapping) != {"A", "B", "C"}:
        raise ValueError(
            "Automatic tri-stereo normalization currently expects prepared image IDs "
            "A, B and C. Found: " + ", ".join(sorted(mapping))
        )

    identity = all(old == new for old, new in mapping.items())
    if identity:
        log.write("Tri-stereo view assignment already normalized: A=F, B=M, C=B.")
    else:
        log.write("Normalizing prepared tri-stereo files by DIM acquisition time:")
        for old, new in mapping.items():
            log.write(f"  {old} -> {new}")

        staged = []
        # First move every existing source to a collision-safe temporary name.
        for old, new in mapping.items():
            if old == new:
                continue
            for source, parent, pattern in _view_asset_specs(settings, old):
                if not source.exists():
                    continue
                temp = parent / f".__vieworder_tmp__{old}__{source.name}"
                if temp.exists():
                    temp.unlink()
                source.replace(temp)
                destination = parent / pattern.format(view=new)
                staged.append((temp, destination))

        # Then place all staged files under their normalized workflow IDs.
        for temp, destination in staged:
            if destination.exists():
                destination.unlink()
            temp.replace(destination)
            log.write(f"  renamed -> {destination}")

    assignment_csv = settings.metadata_dir / f"{settings.project_name}_view_assignment.csv"
    assignment_df.to_csv(assignment_csv, index=False)

    # Persist the normalization map alongside the user's original folder slots.
    config_path = settings.project_dir / "project_settings.json"
    if config_path.exists():
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        payload["normalized_view_assignment"] = {
            row["assigned_workflow_id"]: {
                "role": row["view_role"],
                "figure_symbol": row["figure_symbol"],
                "input_label_before_normalization": row["input_label_before_normalization"],
                "source_acquisition_folder": row["source_acquisition_folder"],
                "acquisition_datetime": row["acquisition_datetime"],
            }
            for row in assignment_df.to_dict("records")
        }
        config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return mapping, assignment_df, assignment_csv


def _tri_stereo_normalization_ready(settings: ProjectSettings) -> bool:
    """Return True when an existing project already records F/M/B normalization.

    New projects store the assignment in project_settings.json.  Older/reopened
    projects may already have the persisted view-assignment CSV, so that file is
    also accepted after validating that it assigns A/B/C exactly once.  This
    avoids forcing Metadata and geometry to be rerun merely because the notebook
    was closed after normalization had already completed.
    """
    if settings.acquisition_mode != "tri_stereo":
        return True

    config_path = settings.project_dir / "project_settings.json"
    if config_path.is_file():
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        assignment = payload.get("normalized_view_assignment")
        if isinstance(assignment, dict) and set(assignment) == {"A", "B", "C"}:
            return True

    assignment_csv = settings.metadata_dir / f"{settings.project_name}_view_assignment.csv"
    if assignment_csv.is_file():
        try:
            df = pd.read_csv(assignment_csv)
            if "assigned_workflow_id" in df.columns:
                values = [str(v).strip() for v in df["assigned_workflow_id"].dropna()]
                if len(values) == 3 and set(values) == {"A", "B", "C"}:
                    return all(
                        (settings.merged_dir / f"{view}.tif").is_file()
                        and _prepared_rpc_path(settings.merged_dir, view).is_file()
                        for view in ("A", "B", "C")
                    )
        except Exception:
            pass

    return False


class ImageGeometry:
    def __init__(self, image_id, along, across, azimuth):
        self.image_id = image_id
        self.scan = radians(float(along))
        self.ortho = radians(float(across))
        self.azimuth = radians(float(azimuth))
        self.ortho = -self.ortho
        self.azimuth = -self.azimuth
        self.compute_components()

    def compute_components(self):
        self.s_comp = cos(self.ortho) * sin(self.scan)
        self.o_comp = cos(self.scan) * sin(self.ortho)
        self.z_comp = cos(self.ortho) * cos(self.scan)


def compute_bh_and_stereo_angle(im1, im2):
    delta_az = im2.azimuth - im1.azimuth
    rot_z = [[cos(delta_az), -sin(delta_az), 0], [sin(delta_az), cos(delta_az), 0], [0, 0, 1]]
    p1 = [im1.s_comp, im1.o_comp, im1.z_comp]
    p1_rot = [sum(a * b for a, b in zip(row, p1)) for row in rot_z]
    p2 = [im2.s_comp, im2.o_comp, im2.z_comp]
    dot = sum(a * b for a, b in zip(p1_rot, p2))
    dot = max(min(dot, 1.0), -1.0)
    stereo_angle_rad = acos(dot)
    stereo_angle_deg = degrees(stereo_angle_rad)
    bh = 2.0 * tan(stereo_angle_rad / 2.0)
    return stereo_angle_deg, bh


def build_stereo_geometry_table(metadata_df, pairs):
    required_cols = ["image_id", "INCIDENCE_ANGLE_ALONG_TRACK", "INCIDENCE_ANGLE_ACROSS_TRACK", "AZIMUTH_ANGLE"]
    missing_cols = [c for c in required_cols if c not in metadata_df.columns]
    if missing_cols:
        return pd.DataFrame(columns=["pair", "left_image", "right_image", "stereo_angle_deg", "B_over_H", "geometry_source"])
    metadata_index = metadata_df.set_index("image_id")
    records = []
    for left, right in pairs:
        if left not in metadata_index.index or right not in metadata_index.index:
            records.append({"pair": f"{left}{right}", "left_image": left, "right_image": right, "stereo_angle_deg": None, "B_over_H": None, "geometry_source": "missing metadata"})
            continue
        row_left = metadata_index.loc[left]
        row_right = metadata_index.loc[right]
        values = [row_left["INCIDENCE_ANGLE_ALONG_TRACK"], row_left["INCIDENCE_ANGLE_ACROSS_TRACK"], row_left["AZIMUTH_ANGLE"], row_right["INCIDENCE_ANGLE_ALONG_TRACK"], row_right["INCIDENCE_ANGLE_ACROSS_TRACK"], row_right["AZIMUTH_ANGLE"]]
        if any(safe_float(v) is None for v in values):
            records.append({"pair": f"{left}{right}", "left_image": left, "right_image": right, "stereo_angle_deg": None, "B_over_H": None, "geometry_source": "missing angle value"})
            continue
        im1 = ImageGeometry(left, row_left["INCIDENCE_ANGLE_ALONG_TRACK"], row_left["INCIDENCE_ANGLE_ACROSS_TRACK"], row_left["AZIMUTH_ANGLE"])
        im2 = ImageGeometry(right, row_right["INCIDENCE_ANGLE_ALONG_TRACK"], row_right["INCIDENCE_ANGLE_ACROSS_TRACK"], row_right["AZIMUTH_ANGLE"])
        stereo_angle_deg, bh = compute_bh_and_stereo_angle(im1, im2)
        records.append({"pair": f"{left}{right}", "left_image": left, "right_image": right, "stereo_angle_deg": round(stereo_angle_deg, 3), "B_over_H": round(bh, 3), "geometry_source": "incidence_angles_and_azimuth"})
    return pd.DataFrame(records)


PREFERRED_METADATA_ORDER = ["view_order", "workflow_assignment", "tif_file", "rpc_file", "dim_file", "IMAGING_DATE", "IMAGING_TIME", "NBANDS", "NBITS", "FOCAL_LENGTH", "AZIMUTH_ANGLE", "VIEWING_ANGLE_ACROSS_TRACK", "VIEWING_ANGLE_ALONG_TRACK", "VIEWING_ANGLE", "INCIDENCE_ANGLE_ALONG_TRACK", "INCIDENCE_ANGLE_ACROSS_TRACK", "INCIDENCE_ANGLE", "SUN_AZIMUTH", "SUN_ELEVATION"]


def run_metadata_geometry(settings: ProjectSettings, iou_threshold: float = 0.05, custom_pairs_text: str = ""):
    create_project_folders(settings)
    work_dir = settings.merged_dir
    log_path = settings.log_dir / "metadata_geometry.log"
    with WorkflowLog(log_path) as log:
        try:
            image_ids = find_image_ids(str(work_dir))
            if len(image_ids) < 2:
                raise RuntimeError(
                    f"At least two prepared image/RPC pairs are required in {work_dir}. "
                    f"Found: {image_ids}"
                )

            # Read DIM metadata before pair geometry so a tri-stereo dataset can
            # be normalized into the fixed workflow convention used by the
            # representative figure and every later ASP stage:
            # A = Forward (F), B = Middle / near-nadir (M), C = Backward (B).
            initial_metadata_df = build_image_metadata_table(str(work_dir), image_ids)
            assignment_df = pd.DataFrame()
            assignment_csv = None
            assignment_mapping = {}

            if settings.acquisition_mode == "tri_stereo":
                assignment_mapping, assignment_df, assignment_csv = (
                    _normalize_tri_stereo_prepared_files(
                        settings,
                        initial_metadata_df,
                        log,
                    )
                )
                image_ids = find_image_ids(str(work_dir))

            pairs_arg = [
                x
                for x in custom_pairs_text.replace(',', ' ').split()
                if x.strip()
            ]
            user_pairs = parse_pairs_argument(pairs_arg)
            pairs = list(combinations(image_ids, 2)) if user_pairs is None else user_pairs

            log.write(f"Detected normalized prepared image IDs: {image_ids}")
            if settings.acquisition_mode == "tri_stereo":
                log.write("Workflow convention: A=Forward (F), B=Middle/near-nadir (M), C=Backward (B)")
            log.write(f"Pairs: {pairs}")

            footprints = build_footprints(str(work_dir), image_ids)
            overlap_df = compute_overlap_table(footprints, pairs, iou_threshold)
            metadata_df = build_image_metadata_table(str(work_dir), image_ids)

            if settings.acquisition_mode == "tri_stereo" and not metadata_df.empty:
                role_by_id = {
                    "A": ("Forward", "F"),
                    "B": ("Middle / near-nadir", "M"),
                    "C": ("Backward", "B"),
                }
                metadata_df["workflow_assignment"] = metadata_df["image_id"].map(
                    lambda x: (
                        f"{x} = {role_by_id[str(x)][0]} ({role_by_id[str(x)][1]})"
                        if str(x) in role_by_id
                        else str(x)
                    )
                )

            geometry_df = build_stereo_geometry_table(metadata_df, pairs)
            overlap_csv = settings.metadata_dir / f"{settings.project_name}_overlap_pairs.csv"
            metadata_csv = settings.metadata_dir / f"{settings.project_name}_image_metadata.csv"
            geometry_csv = settings.metadata_dir / f"{settings.project_name}_stereo_geometry.csv"
            overlap_df.to_csv(overlap_csv, index=False)
            metadata_df.to_csv(metadata_csv, index=False)
            geometry_df.to_csv(geometry_csv, index=False)

            if 'image_id' not in metadata_df.columns:
                metadata_vertical_df = pd.DataFrame()
            else:
                metadata_vertical_df = (
                    metadata_df.set_index('image_id')
                    .T.reset_index()
                    .rename(columns={'index': 'Parameter'})
                )
                existing_preferred = [
                    p
                    for p in PREFERRED_METADATA_ORDER
                    if p in metadata_vertical_df['Parameter'].values
                ]
                remaining = [
                    p
                    for p in metadata_vertical_df['Parameter'].values
                    if p not in existing_preferred
                ]
                metadata_vertical_df = (
                    metadata_vertical_df.set_index('Parameter')
                    .loc[existing_preferred + remaining]
                    .reset_index()
                )

            return {
                'work_dir': work_dir,
                'overlap': overlap_df,
                'metadata': metadata_vertical_df,
                'geometry': geometry_df,
                'assignment': assignment_df,
                'assignment_mapping': assignment_mapping,
                'assignment_csv': assignment_csv,
                'overlap_csv': overlap_csv,
                'metadata_csv': metadata_csv,
                'geometry_csv': geometry_csv,
                'log_path': log_path,
            }
        except Exception:
            log.write(traceback.format_exc())
            raise


# ============================================================
# UI
# ============================================================

class ProjectSetupUI:
    def __init__(self):
        import ipywidgets as widgets
        from IPython.display import display, Image
        self.widgets = widgets
        self.display_fn = display
        self.ImageClass = Image
        self.last_prepare = None
        self.last_metadata = None
        self._loaded_project_dir = None
        style = {"description_width": "185px"}
        wide = widgets.Layout(width="780px")
        # Publication/software cover is provided by the first Markdown cell
        # of the user-facing notebook, so it is visible before Python runs.
        self.title = widgets.HTML("")
        self.project_name = widgets.Text(value="Berarde_Aug24", description="Project name:", style=style, layout=wide)
        self.output_base = widgets.Text(value=str(Path.home()), description="Output base folder:", style=style, layout=wide)
        self.platform = widgets.Combobox(
            options=[
                "PHR1A",
                "PHR1B",
                "PNEO3",
                "PNEO4",
                "SPOT6",
                "SPOT7",
            ],
            value="PHR1A",
            ensure_option=False,
            description="Platform:",
            placeholder="Select or type platform code",
            style=style,
            layout=wide,
        )
        self.acquisition_mode = widgets.ToggleButtons(options=[("Stereo — A/B", "stereo"), ("Tri-stereo — A/B/C", "tri_stereo")], value="tri_stereo", description="Acquisition:", style=style)
        self.acquisition_A = widgets.Text(description="Input folder 1:", placeholder="/path/to/Pleiades_1/IMG_PHR1A_P_001", style=style, layout=wide)
        self.acquisition_B = widgets.Text(description="Input folder 2:", placeholder="/path/to/Pleiades_2/IMG_PHR1A_P_001", style=style, layout=wide)
        self.acquisition_C = widgets.Text(description="Input folder 3:", placeholder="/path/to/Pleiades_3/IMG_PHR1A_P_001", style=style, layout=wide)
        self.merge_tiles = widgets.Checkbox(value=False, description="Merge selected image tiles", indent=False)
        self.tile_ids = widgets.Text(value="R1C1", description="Tile ID(s):", style=style, layout=wide)
        self.crop_enabled = widgets.Checkbox(value=False, description="Crop prepared images to an AOI", indent=False)
        self.aoi_vector = widgets.Text(description="AOI vector:", placeholder="/path/to/aoi.shp", style=style, layout=wide)
        self.rpc_height = widgets.FloatText(value=2500.0, description="RPC height (m):", style=style)
        self.buffer_px = widgets.IntText(value=500, description="Crop buffer (px):", style=style)
        self.spacing_deg = widgets.FloatText(value=0.00005, description="AOI spacing (deg):", style=style)
        self.overwrite = widgets.Checkbox(value=False, description="Overwrite existing outputs", indent=False)

        # Resume an existing project without repeating Prepare data. The
        # complete input/source paths are restored from project_settings.json.
        self.existing_project_dir = widgets.Text(
            description="Existing project folder:",
            placeholder="/path/to/previous/project",
            style=style,
            layout=wide,
        )
        self.load_existing_project = widgets.Button(
            description="Load / resume existing project",
            button_style="info",
            icon="folder-open",
            layout=widgets.Layout(width="285px", height="40px"),
        )
        self.resume_project_status = widgets.HTML(
            "<span style='color:#666;'>New project. Existing projects can be resumed from their saved project_settings.json.</span>"
        )

        # New-project reset only. This never deletes project files on disk.
        self.start_clean_project = widgets.Button(
            description="Start new project / clear form",
            button_style="",
            icon="refresh",
            layout=widgets.Layout(width="260px", height="38px"),
        )
        self.clean_project_status = widgets.HTML("")

        # Context-sensitive validation messages. Keep the interface clean: these
        # panels are hidden unless the current inputs require user attention.
        self.tile_merge_caution = widgets.HTML("")
        self.project_exists_notice = widgets.HTML("")

        self.run_stage1 = widgets.Button(description="Run prepare data", button_style="info", icon="play", layout=widgets.Layout(width="240px", height="42px"))
        self.stage1_progress = widgets.IntProgress(value=0, min=0, max=100, description="Progress:", style={"description_width": "80px"}, layout=widgets.Layout(width="720px"))
        self.stage1_progress_text = widgets.HTML("<span style='color:#666;'>Waiting.</span>")
        self.stage1_summary = widgets.HTML()
        self.stage1_summary_details = widgets.Accordion(children=[self.stage1_summary])
        self.stage1_summary_details.set_title(0, "Run details / processing summary")
        self.stage1_summary_details.selected_index = None
        self.stage1_preview = widgets.Output(layout=widgets.Layout(border="1px solid #ddd", padding="6px", width="100%"))
        self.iou_threshold = widgets.FloatText(value=0.05, description="IoU threshold:", style=style)
        self.custom_pairs = widgets.Text(value="", description="Custom pairs:", placeholder="Leave blank for automatic pairs; e.g. A:B A:C B:C", style=style, layout=wide)
        self.run_stage2 = widgets.Button(description="Run metadata and geometry", button_style="info", icon="table", layout=widgets.Layout(width="280px", height="42px"))
        self.stage2_summary = widgets.HTML()
        self.stage2_summary_details = widgets.Accordion(children=[self.stage2_summary])
        self.stage2_summary_details.set_title(0, "Run details / processing summary")
        self.stage2_summary_details.selected_index = None
        self.stage2_tables_box = widgets.VBox()
        # Persistent concept-figure container.
        #
        # Render through responsive HTML rather than ipywidgets.Image.
        # Some Jupyter frontends clip the vertical extent of very wide Image
        # widgets, which makes the figure appear incomplete.
        self.concept_figure = widgets.HTML(
            value="",
            layout=widgets.Layout(
                width="100%",
                margin="4px 0 12px 0",
            ),
        )

        self.metadata_geometry_header = widgets.HTML(value="")

        # Persistent runtime summary. One row is kept for each workflow stage;
        # rerunning a stage replaces its previous runtime rather than appending
        # duplicate rows. The CSV is saved under metadata/runtime_summary.csv.
        self._runtime_stage_starts = {}
        self._runtime_lock = threading.Lock()
        self.runtime_summary_table = widgets.HTML(value="")
        self.runtime_summary_path = widgets.HTML(value="")

        self.resume_project_rows = [
            self._row(
                self.existing_project_dir,
                "Select the project folder created by an earlier run. Its project_settings.json restores the project name, original input folders, tile selection, AOI settings, and output base path. Prepared products already on disk are reused; Prepare data does not need to be rerun.",
            ),
            self.load_existing_project,
            self.resume_project_status,
        ]

        self.common_rows = [
            self._row(self.project_name, "Short project name. A folder with this name is created inside the Output base folder. Example: Berarde_Aug24."),
            self._row(self.output_base, "Parent folder only. Do not add the project name or merged_tiles. Example: /mnt/summer/USERS/KOAI/Software."),
            self._row(self.platform, "Sensor/platform code used to find RPC_<Platform>*.XML and DIM_<Platform>*.XML. Presets include Pléiades (PHR1A/PHR1B), Pléiades Neo (PNEO3/PNEO4), and SPOT 6/7; another compatible code may also be typed."),
            self._row(self.acquisition_mode, "Stereo requires two input acquisitions; tri-stereo requires three."),
            self._row(self.acquisition_A, "Input acquisition folder containing the selected Pléiades/compatible DIMAP image product."),
            self._row(self.acquisition_B, "Input acquisition folder containing the selected Pléiades/compatible DIMAP image product."),
            self._row(self.acquisition_C, "Third input acquisition folder, used only when Tri-stereo is selected."),
            self._row(self.merge_tiles, "OFF: use one selected tile only. ON: merge several DIMAP tiles into one A/B/C image. Example ON: R1C1,R1C2,R2C1,R2C2."),
            self._row(self.tile_ids, "Tile ID(s) searched in every acquisition. When merge is OFF, use exactly one, e.g. R1C1. When ON, separate IDs by commas."),
            self._row(self.crop_enabled, "OFF: use full prepared A/B(/C). ON: additionally create A_crop/B_crop/C_crop in merged_tiles/cropped_images. Raw data are never modified."),
        ]
        self.crop_rows = [
            self._row(self.aoi_vector, "AOI vector used by the original RPC crop logic. It must have a valid CRS."),
            self._row(self.rpc_height, "Representative terrain height used by RPC projection. Original default: 2500 m."),
            self._row(self.buffer_px, "Extra pixels around the projected AOI crop. Original default: 500 px."),
            self._row(self.spacing_deg, "AOI boundary densification spacing. Original default: 0.00005 degrees."),
        ]
        self.overwrite_row = self._row(self.overwrite, "Enable only when you intentionally want to replace existing outputs.")
        self.block2_rows = [
            self._row(self.iou_threshold, "Minimum IoU used to mark an overlap pair as valid. Original default: 0.05."),
            self._row(self.custom_pairs, "Optional. Leave blank to use all automatic image pairs. For custom cases use LEFT:RIGHT, e.g. A_1:B_1 A_2:B_2."),
        ]
        self.crop_box = widgets.VBox(self.crop_rows)
        self.acquisition_mode.observe(self._update_visibility, names="value")
        self.crop_enabled.observe(self._update_visibility, names="value")
        self.merge_tiles.observe(self._update_tile_validation_message, names="value")
        self.tile_ids.observe(self._update_tile_validation_message, names="value")
        self.load_existing_project.on_click(self._on_load_existing_project)
        self.start_clean_project.on_click(self._on_start_clean_project)
        self.run_stage1.on_click(self._on_stage1)
        self.run_stage2.on_click(self._on_stage2)
        self._update_visibility()
        self.container = widgets.VBox([
            self.title,
            widgets.HTML("<hr><div style='margin:12px 0 10px 0;padding:12px 14px;border-left:6px solid #1976d2;background:#eef5fb;border-radius:3px;'><div style='font-size:20px;font-weight:700;color:#17324d;'>Prepare data</div><div style='color:#5f6b76;font-size:13px;margin-top:5px;line-height:1.5;'>Organize the project folders, prepare A/B(/C), optionally crop to an AOI, and save a compact image preview.</div></div>"),
            widgets.HTML("<div style='margin:2px 0 7px 0;font-weight:700;color:#444;'>New project</div>"),
            self.start_clean_project,
            self.clean_project_status,
            widgets.HTML("<hr style='margin:12px 0 10px 0;border:none;border-top:1px solid #ddd;'>"),
            widgets.HTML("<b>1. Project</b>"),
            widgets.HTML("<div style='margin:4px 0 8px 0;color:#555;font-size:12px;'><b>Resume:</b> load a previous project to restore its saved input locations and continue from existing products without rerunning completed stages.</div>"),
            *self.resume_project_rows,
            widgets.HTML("<div style='margin:10px 0 5px 0;color:#666;font-size:12px;'>Or define a new project:</div>"),
            *self.common_rows[:3],
            self.project_exists_notice,
            widgets.HTML("<br><b>2. Input acquisitions</b>"), self.common_rows[3], *self.common_rows[4:7],
            widgets.HTML("<br><b>3. Image tile preparation</b>"), *self.common_rows[7:9], self.tile_merge_caution,
            widgets.HTML("<br><b>4. Optional AOI crop</b>"), self.common_rows[9], self.crop_box,
            widgets.HTML("<br><b>5. Output protection</b>"), self.overwrite_row,
            widgets.HTML("<br>"), self.run_stage1, self.stage1_progress, self.stage1_progress_text, self.stage1_summary_details, self.stage1_preview,
            self.metadata_geometry_header,
            self.concept_figure,
            *self.block2_rows, widgets.HTML("<br>"), self.run_stage2, self.stage2_summary_details, self.stage2_tables_box
        ], layout=widgets.Layout(width="100%"))
        self._display_concept_figure()
        self.project_name.observe(self._on_runtime_project_change, names="value")
        self.output_base.observe(self._on_runtime_project_change, names="value")
        self._update_tile_validation_message()
        self._update_existing_project_notice()

        # Restore the last project used from this workspace when possible.
        # This only restores widget values and detects existing products; it
        # never reruns scientific processing automatically.
        self._try_restore_last_project()
        self._refresh_runtime_summary()

    def _help_icon(self, text):
        safe = html.escape(text, quote=True)
        return self.widgets.HTML(value=(f"<span title=\"{safe}\" style='cursor:help; font-size:18px; color:#336699; padding-left:5px;'>ⓘ</span>"), layout=self.widgets.Layout(width="32px"))

    def _row(self, widget, help_text):
        return self.widgets.HBox([widget, self._help_icon(help_text)], layout=self.widgets.Layout(width="100%", align_items="center"))

    def _parsed_tile_ids(self):
        return tuple(
            part.strip().upper()
            for part in self.tile_ids.value.replace(";", ",").split(",")
            if part.strip()
        )

    def _update_tile_validation_message(self, change=None):
        tiles = self._parsed_tile_ids()
        if not tiles:
            self.tile_merge_caution.value = (
                "<div style='margin:6px 0 8px 205px;padding:8px 10px;"
                "border-left:4px solid #b00020;background:#fff4f4;color:#444;"
                "max-width:780px;font-size:12px;line-height:1.45;'>"
                "<b>Tile selection required.</b> Enter <code>R1C1</code>, or enable merging and include <code>R1C1</code> with the additional tile IDs.</div>"
            )
            return

        has_r1c1 = "R1C1" in tiles
        if self.merge_tiles.value and not has_r1c1:
            self.tile_merge_caution.value = (
                "<div style='margin:6px 0 8px 205px;padding:8px 10px;"
                "border-left:4px solid #b00020;background:#fff4f4;color:#444;"
                "max-width:780px;font-size:12px;line-height:1.45;'>"
                "<b>Caution — tiled DIMAP products:</b> R1C1 is missing. Merging is enabled, so include <code>R1C1</code> together with every additional tile required by the same DIMAP image product (for example <code>R1C1,R1C2</code>).</div>"
            )
        elif (not self.merge_tiles.value) and (len(tiles) != 1 or tiles[0] != "R1C1"):
            selected = ", ".join(html.escape(tile) for tile in tiles)
            self.tile_merge_caution.value = (
                "<div style='margin:6px 0 8px 205px;padding:8px 10px;"
                "border-left:4px solid #b00020;background:#fff4f4;color:#444;"
                "max-width:780px;font-size:12px;line-height:1.45;'>"
                f"<b>Caution — tiled DIMAP products:</b> Invalid selection: {selected}. "
                "If the image extends beyond <code>R1C1</code>, enable <b>Merge selected image tiles</b> and include <code>R1C1</code> with the additional tile IDs. Do not prepare a later tile alone.</div>"
            )
        else:
            self.tile_merge_caution.value = ""

    def _update_existing_project_notice(self, change=None):
        try:
            project_name = self.project_name.value.strip()
            output_base = self.output_base.value.strip()
            if not project_name or not output_base:
                self.project_exists_notice.value = ""
                return
            project_dir = (Path(output_base).expanduser() / project_name).resolve()
            loaded = Path(self._loaded_project_dir).resolve() if self._loaded_project_dir else None
            if loaded is not None and loaded == project_dir:
                self.project_exists_notice.value = ""
                return
            if not project_dir.exists():
                self.project_exists_notice.value = ""
                return
            config = project_dir / "project_settings.json"
            if config.is_file():
                message = (
                    "<b>Project already exists.</b> Use <b>Load / resume existing project</b> above to restore its saved inputs and results, or choose a different project name."
                )
            else:
                message = (
                    "<b>Output folder already exists.</b> It is not recognized as a saved workflow project. Choose a different project name or review the folder before preparing data."
                )
            self.project_exists_notice.value = (
                "<div style='margin:6px 0 8px 205px;padding:8px 10px;"
                "border-left:4px solid #d28b00;background:#fffaf0;color:#444;"
                "max-width:780px;font-size:12px;line-height:1.45;'>"
                + message + "<br><code>" + html.escape(str(project_dir)) + "</code></div>"
            )
        except Exception:
            self.project_exists_notice.value = ""

    def _update_visibility(self, change=None):
        is_tri = self.acquisition_mode.value == "tri_stereo"
        self.common_rows[6].layout.display = "" if is_tri else "none"
        self.crop_box.layout.display = "" if self.crop_enabled.value else "none"

        if is_tri:
            self.acquisition_A.description = "Input folder 1:"
            self.acquisition_B.description = "Input folder 2:"
            self.acquisition_C.description = "Input folder 3:"
            metadata_message = (
                "This step reads the full prepared files in <code>merged_tiles</code>. "
                "For tri-stereo, DIM acquisition times determine the viewing order and normalize "
                "the prepared files to <b>A = Forward (F)</b>, "
                "<b>B = Near-nadir / Middle (M)</b>, and <b>C = Backward (B)</b>; "
                "cropped copies are relabeled consistently."
            )
        else:
            self.acquisition_A.description = "Input A:"
            self.acquisition_B.description = "Input B:"
            self.acquisition_C.description = "Input C:"
            metadata_message = (
                "This step reads the full prepared files in <code>merged_tiles</code>. "
                "For stereo, the two prepared images are handled simply as <b>A</b> and <b>B</b>; "
                "cropped copies are relabeled consistently."
            )

        self.metadata_geometry_header.value = (
            "<hr><div style='margin:12px 0 10px 0;padding:12px 14px;"
            "border-left:6px solid #1976d2;background:#eef5fb;border-radius:3px;'>"
            "<div style='font-size:20px;font-weight:700;color:#17324d;'>Metadata and geometry</div>"
            "<div style='color:#5f6b76;font-size:13px;margin-top:5px;line-height:1.5;'>"
            + metadata_message + "</div></div>"
        )

    def _display_concept_figure(self):
        """
        Display the complete stereo/tri-stereo concept figure responsively.

        The PNG is embedded as a data URI in an HTML <img> element so its
        complete aspect ratio is preserved across JupyterLab/Notebook
        frontends.
        """
        module_dir = Path(__file__).resolve().parent

        candidates = [
            module_dir / "figures" / "overview_mountain_notebook.png",
            Path.cwd() / "figures" / "overview_mountain_notebook.png",
        ]

        img_path = next(
            (
                path
                for path in candidates
                if path.is_file()
            ),
            None,
        )

        if img_path is not None:
            encoded = base64.b64encode(
                img_path.read_bytes()
            ).decode("ascii")

            self.concept_figure.value = (
                "<div style='"
                "box-sizing:border-box;"
                "width:100%;"
                "border:1px solid #ddd;"
                "padding:8px;"
                "background:#fff;"
                "overflow:visible;"
                "'>"
                "<img "
                f"src='data:image/png;base64,{encoded}' "
                "alt='Stereo and tri-stereo acquisition geometry' "
                "style='"
                "display:block;"
                "width:100%;"
                "height:auto;"
                "max-width:1500px;"
                "margin:0 auto;"
                "object-fit:contain;""max-height:none;""min-height:0;"
                "'>"
                "</div>"
            )

        else:
            searched = "<br>".join(
                f"<code>{html.escape(str(path))}</code>"
                for path in candidates
            )

            self.concept_figure.value = (
                "<div style='padding:10px 12px;"
                "border-left:4px solid #d58b00;"
                "background:#fffaf0;color:#555;width:100%;'>"
                "<b>Concept figure not found.</b><br>"
                "Expected: "
                "<code>figures/overview_mountain_notebook.png</code>.<br><br>"
                "<b>Searched:</b><br>"
                f"{searched}"
                "</div>"
            )


    def _clear_downstream_project_state(self):
        """Hook overridden by FullProjectSetupUI to clear downstream project state."""
        return None

    def _on_start_clean_project(self, _):
        """Reset notebook/UI state for a new project without deleting anything."""
        if getattr(self, "_execution_busy", lambda: False)():
            self.clean_project_status.value = (
                "<span style='color:#b00020;font-size:12px;'>Stop the active workflow process before starting a new project.</span>"
            )
            return

        try:
            pointer = self._last_project_pointer_path()
            if pointer.exists():
                pointer.unlink()

            self._loaded_project_dir = None
            self.project_name.value = ""
            self.output_base.value = str(Path.home())
            self.platform.value = "PHR1A"
            self.acquisition_mode.value = "tri_stereo"
            self.acquisition_A.value = ""
            self.acquisition_B.value = ""
            self.acquisition_C.value = ""
            self.merge_tiles.value = False
            self.tile_ids.value = "R1C1"
            self.crop_enabled.value = False
            self.aoi_vector.value = ""
            self.rpc_height.value = 2500.0
            self.buffer_px.value = 500
            self.spacing_deg.value = 0.00005
            self.overwrite.value = False
            self.existing_project_dir.value = ""

            self.last_prepare = None
            self.last_metadata = None
            self.stage1_progress.value = 0
            self.stage1_progress.bar_style = ""
            self.stage1_progress_text.value = "<span style='color:#666;'>Waiting.</span>"
            self.stage1_summary.value = ""
            self.stage1_summary_details.selected_index = None
            self.stage1_preview.clear_output()
            self.stage2_summary.value = ""
            self.stage2_summary_details.selected_index = None
            self.stage2_tables_box.children = ()
            self.resume_project_status.value = (
                "<span style='color:#666;'>New project mode. Define the inputs below, then run Prepare data.</span>"
            )
            self.run_stage1.description = "Run prepare data"
            self.run_stage1.button_style = "info"
            self.clean_project_status.value = ""
            self._refresh_runtime_summary()
            self._clear_downstream_project_state()
            self._update_tile_validation_message()
            self._update_existing_project_notice()
        except Exception as exc:
            self.clean_project_status.value = (
                "<div style='margin:6px 0;padding:8px 10px;border-left:4px solid #b00020;"
                "background:#fff4f4;color:#444;font-size:12px;'>"
                "<b>New-project reset stopped.</b><br>"
                + html.escape(type(exc).__name__ + ": " + str(exc))
                + "</div>"
            )

    def _last_project_pointer_path(self):
        return Path.cwd() / ".pleiades_asp_last_project.json"

    def _remember_project(self, settings):
        """Remember only the project folder; scientific settings stay in the project."""
        try:
            pointer = self._last_project_pointer_path()
            pointer.write_text(
                json.dumps({"project_dir": str(settings.project_dir)}, indent=2),
                encoding="utf-8",
            )
        except Exception:
            # Resume convenience must never make a scientific run fail.
            pass

    def _apply_loaded_project_settings(self, settings):
        """Populate project/setup widgets from a saved ProjectSettings object."""
        self.project_name.value = settings.project_name
        self.output_base.value = settings.output_base
        self.platform.value = settings.platform
        self.acquisition_mode.value = settings.acquisition_mode
        self.acquisition_A.value = settings.acquisition_A
        self.acquisition_B.value = settings.acquisition_B
        self.acquisition_C.value = settings.acquisition_C
        self.merge_tiles.value = bool(settings.merge_tiles)
        self.tile_ids.value = ",".join(settings.tile_ids)
        self.crop_enabled.value = bool(settings.crop_enabled)
        self.aoi_vector.value = settings.aoi_vector
        self.rpc_height.value = float(settings.rpc_height)
        self.buffer_px.value = int(settings.buffer_px)
        self.spacing_deg.value = float(settings.spacing_deg)
        self.overwrite.value = bool(settings.overwrite)
        self.existing_project_dir.value = str(settings.project_dir)
        self._update_visibility()

    def _restore_prepared_image_preview(self, settings):
        """Restore the saved prepared-image preview for an existing project.

        Loading/resuming a project must not rerun image preparation.  This
        method only re-displays the preview PNG already saved by Prepare data.
        If an older project has prepared rasters but no saved preview image, a
        lightweight preview is recreated directly from those existing rasters;
        no merge, crop, metadata, or ASP processing is executed.
        """
        status = inspect_existing_project(settings)
        self.stage1_preview.clear_output()

        if not status["prepared_ready"]:
            return False

        cropped_ready = bool(settings.crop_enabled) and all(
            (settings.cropped_dir / f"{name}_crop.tif").is_file()
            for name in settings.image_names
        )
        stage = "cropped" if cropped_ready else "prepared"
        active_images = {
            name: (
                settings.cropped_dir / f"{name}_crop.tif"
                if cropped_ready
                else settings.merged_dir / f"{name}.tif"
            )
            for name in settings.image_names
        }

        png = settings.figure_dir / f"{stage}_images_preview.png"
        pdf = settings.figure_dir / f"{stage}_images_preview.pdf"
        preview_note = "Saved preview restored from the existing project."

        if png.is_file():
            with self.stage1_preview:
                self.display_fn(self.ImageClass(filename=str(png)))
        else:
            try:
                preview = make_preview(settings, cropped=cropped_ready)
                png = preview["png"]
                pdf = preview["pdf"]
                with self.stage1_preview:
                    self.display_fn(preview["figure"])
                plt.close(preview["figure"])
                preview_note = (
                    "Preview recreated from the existing prepared rasters only; "
                    "Prepare data was not rerun."
                )
            except Exception as exc:
                with self.stage1_preview:
                    self.display_fn(self.widgets.HTML(
                        "<div style='padding:9px 11px;border-left:4px solid #d28b00;"
                        "background:#fffaf0;color:#555;font-size:12px;'>"
                        "Prepared images were detected, but their preview could not be restored: "
                        + html.escape(type(exc).__name__ + ": " + str(exc))
                        + "</div>"
                    ))
                return False

        active_lines = "<br>".join(
            f"<code>{name}: {html.escape(str(path))}</code>"
            for name, path in active_images.items()
        )
        self.stage1_summary_details.selected_index = None
        self.stage1_summary.value = (
            "<div style='margin:10px 0;padding:10px;border-left:4px solid #2e7d32;"
            "background:#f4fbf4;color:#444;'>"
            "<b>✓ Existing prepared images recovered.</b><br>"
            + html.escape(preview_note)
            + "<br><br><b>Active images:</b><br>" + active_lines
            + "<br><br><b>Preview PNG:</b> <code>" + html.escape(str(png)) + "</code>"
            + ("<br><b>Preview PDF:</b> <code>" + html.escape(str(pdf)) + "</code>" if pdf.is_file() else "")
            + "</div>"
        )
        return True

    def _refresh_resume_project_status(self, settings=None, *, auto=False):
        if settings is None:
            try:
                settings = self._build_settings()
            except Exception:
                return

        status = inspect_existing_project(settings)
        if status["prepared_ready"]:
            prepared_text = (
                "<b>✓ Prepared A/B(/C) data detected.</b> You can continue with "
                "Metadata and geometry or later stages without rerunning Prepare data."
            )
            self.run_stage1.description = "Re-run prepare data"
            self.run_stage1.button_style = "warning"
        else:
            prepared_text = (
                "Prepared data are incomplete; use Run prepare data before stages that "
                "need the prepared images."
            )
            self.run_stage1.description = "Run prepare data"
            self.run_stage1.button_style = "info"

        metadata_text = (
            "<br><b>✓ Metadata/geometry products detected.</b> They can be reused."
            if status["metadata_ready"]
            else "<br>Metadata/geometry products were not detected yet."
        )
        final_text = (
            "<br><b>✓ Final DSM product table detected.</b>"
            if status["final_dsm_ready"]
            else ""
        )
        lead = "Last project restored automatically." if auto else "Existing project loaded."
        color = "#2e7d32" if status["prepared_ready"] else "#8a5a00"
        background = "#f4fbf4" if status["prepared_ready"] else "#fffaf0"
        self.resume_project_status.value = (
            f"<div style='margin:6px 0 8px 0;padding:9px 11px;border-left:4px solid {color};"
            f"background:{background};color:#444;font-size:12px;line-height:1.5;'>"
            f"<b>{html.escape(lead)}</b><br>"
            f"<code>{html.escape(str(settings.project_dir))}</code><br>"
            + prepared_text + metadata_text + final_text +
            "<br><span style='color:#666;'>Nothing is rerun automatically. Existing files "
            "remain on disk and are used as prerequisites when you choose a later stage.</span>"
            "</div>"
        )

    def _load_existing_project_folder(self, project_dir, *, auto=False):
        settings = load_project_config(project_dir)
        self._loaded_project_dir = str(settings.project_dir.resolve())
        self._apply_loaded_project_settings(settings)
        self._remember_project(settings)
        self._refresh_runtime_summary(settings)
        self._refresh_resume_project_status(settings, auto=auto)
        self._restore_prepared_image_preview(settings)

        # FullProjectSetupUI adds this method after the base interface is built.
        # On a manual load it is already available; during base-class startup
        # the derived class restores processing state once its controls exist.
        restore = getattr(self, "_restore_processing_state_from_project", None)
        if callable(restore):
            restore(settings)
        self._update_existing_project_notice()
        return settings

    def _on_load_existing_project(self, _):
        try:
            project_dir = self.existing_project_dir.value.strip()
            if not project_dir:
                raise ValueError("Select an existing project folder first.")
            self._load_existing_project_folder(project_dir, auto=False)
        except Exception as exc:
            self.resume_project_status.value = (
                "<div style='margin:6px 0 8px 0;padding:9px 11px;border-left:4px solid #b00020;"
                "background:#fff4f4;color:#444;font-size:12px;line-height:1.5;'>"
                "<b>✗ Existing project could not be loaded.</b><br>"
                + html.escape(type(exc).__name__ + ": " + str(exc)) +
                "</div>"
            )

    def _try_restore_last_project(self):
        pointer = self._last_project_pointer_path()
        if not pointer.is_file():
            return
        try:
            payload = json.loads(pointer.read_text(encoding="utf-8"))
            project_dir = payload.get("project_dir", "")
            if project_dir and Path(project_dir).expanduser().is_dir():
                self._load_existing_project_folder(project_dir, auto=True)
        except Exception:
            # A stale last-project pointer should never prevent a fresh project.
            pass

    def _build_settings(self):
        project_name = self.project_name.value.strip()
        output_base = self.output_base.value.strip()
        platform = self.platform.value.strip()
        if not project_name: raise ValueError("Project name is required.")
        if not output_base: raise ValueError("Output base folder is required.")
        if not platform: raise ValueError("Platform is required.")
        a = self.acquisition_A.value.strip()
        b = self.acquisition_B.value.strip()
        c = self.acquisition_C.value.strip()
        if not a or not b: raise ValueError("Acquisition A and B folders are required.")
        if self.acquisition_mode.value == "tri_stereo" and not c: raise ValueError("Acquisition C is required for tri-stereo.")
        tile_ids = tuple(value.strip().upper() for value in self.tile_ids.value.replace(";", ",").split(",") if value.strip())
        if not tile_ids: raise ValueError("At least one Tile ID is required.")
        return ProjectSettings(project_name=project_name, output_base=output_base, platform=platform, acquisition_mode=self.acquisition_mode.value, acquisition_A=a, acquisition_B=b, acquisition_C=c, merge_tiles=bool(self.merge_tiles.value), tile_ids=tile_ids, crop_enabled=bool(self.crop_enabled.value), aoi_vector=self.aoi_vector.value.strip(), rpc_height=float(self.rpc_height.value), buffer_px=int(self.buffer_px.value), spacing_deg=float(self.spacing_deg.value), overwrite=bool(self.overwrite.value))

    def _runtime_csv_path_from_widgets(self):
        project = str(self.project_name.value).strip()
        base = str(self.output_base.value).strip()
        if not project or not base:
            return None
        return Path(base).expanduser() / project / "metadata" / "runtime_summary.csv"

    def _refresh_runtime_summary(self, settings=None):
        try:
            csv_path = (
                settings.metadata_dir / "runtime_summary.csv"
                if settings is not None
                else self._runtime_csv_path_from_widgets()
            )
            df = _load_runtime_summary(csv_path) if csv_path is not None else _empty_runtime_summary()
        except Exception:
            csv_path = None
            df = _empty_runtime_summary()

        display_df = df[["order", "stage", "last_runtime", "last_completed", "run_count"]].copy()
        display_df.columns = ["#", "Workflow step", "Last runtime", "Last completed", "Runs"]
        display_df["Last runtime"] = display_df["Last runtime"].replace("", "—").fillna("—")
        display_df["Last completed"] = display_df["Last completed"].replace("", "—").fillna("—")
        self.runtime_summary_table.value = self._dataframe_html(display_df)
        self.runtime_summary_path.value = (
            "<div style='margin-top:7px;color:#555;font-size:12px;'>"
            "<b>Runtime CSV:</b> <code>"
            + html.escape(str(csv_path if csv_path is not None else "metadata/runtime_summary.csv"))
            + "</code><br>Each completed stage updates its existing row. Rerunning a stage replaces the previous runtime.</div>"
        )

    def _runtime_begin(self, stage_key, settings=None):
        if settings is None:
            try:
                settings = self._build_settings()
            except Exception:
                settings = None
        with self._runtime_lock:
            self._runtime_stage_starts[str(stage_key)] = (time.perf_counter(), settings)

    def _runtime_finish(self, stage_key, settings=None):
        key = str(stage_key)
        with self._runtime_lock:
            started = self._runtime_stage_starts.pop(key, None)
        if started is None:
            return None
        start_time, stored_settings = started
        elapsed = time.perf_counter() - start_time
        settings = settings or stored_settings
        if settings is None:
            try:
                settings = self._build_settings()
            except Exception:
                return elapsed
        try:
            _record_runtime(settings, key, elapsed)
            self._refresh_runtime_summary(settings)
        except Exception:
            # Runtime bookkeeping is diagnostic only and must never make a
            # successful scientific processing step fail.
            pass
        return elapsed

    def _on_runtime_project_change(self, change=None):
        self._refresh_runtime_summary()
        self._update_existing_project_notice()

    def _set_progress(self, value, message):
        self.stage1_progress.value = int(value)
        self.stage1_progress_text.value = f"<span style='color:#555;'>{html.escape(message)}</span>"

    def _on_stage1(self, _):
        self.stage1_summary.value = ""
        self.stage1_preview.clear_output()
        self.stage1_progress.bar_style = ""
        self.run_stage1.disabled = True
        try:
            settings = self._build_settings()
            self._runtime_begin("prepare_data", settings)
            result = run_prepare_data(settings, progress_callback=self._set_progress)
            self._runtime_finish("prepare_data", settings)
            self.last_prepare = result
            self._remember_project(settings)
            self._refresh_resume_project_status(settings)
            active_lines = "<br>".join(f"<code>{name}: {html.escape(str(path))}</code>" for name, path in result['active_images'].items())
            crop_text = "AOI crop created; cropped images are active." if settings.crop_enabled else "AOI crop not requested; full prepared images are active."
            self.stage1_summary_details.selected_index = None
            self.stage1_summary.value = ("<div style='margin:10px 0;padding:10px;border-left:4px solid #2e7d32;background:#f4fbf4;'>"
                                         "<b>✓ Prepare data completed.</b><br>" + html.escape(crop_text) + "<br><br><b>Active images:</b><br>" + active_lines + "<br><br><b>Preview PNG:</b> <code>" + html.escape(str(result['preview']['png'])) + "</code><br><b>Preview PDF:</b> <code>" + html.escape(str(result['preview']['pdf'])) + "</code><br><b>Detailed log:</b> <code>" + html.escape(str(result['log_path'])) + "</code></div>")
            with self.stage1_preview:
                self.display_fn(result['preview']['figure'])
            plt.close(result['preview']['figure'])
            self.stage1_progress.bar_style = "success"
        except Exception as exc:
            self.stage1_progress.bar_style = "danger"
            try:
                settings = self._build_settings()
                log_path = settings.log_dir / 'prepare_data.log'
            except Exception:
                log_path = Path('(log path unavailable)')
            self.stage1_summary_details.selected_index = 0
            self.stage1_summary.value = ("<div style='margin:10px 0;padding:10px;border-left:4px solid #b00020;background:#fff4f4;'><b>✗ Prepare data stopped.</b><br>" + html.escape(type(exc).__name__ + ': ' + str(exc)) + "<br><br><b>Detailed log:</b> <code>" + html.escape(str(log_path)) + "</code></div>")
        finally:
            self.run_stage1.disabled = False

    def _dataframe_html(self, df):
        if df is None or len(df) == 0:
            return "<div style='padding:10px;color:#666;'>No rows to display.</div>"

        table_html = df.to_html(
            index=False,
            border=0,
            classes="asp-analysis-table",
            na_rep="—",
        )
        return (
            "<style>"
            ".asp-analysis-table{border-collapse:collapse;width:100%;font-size:12px;}"
            ".asp-analysis-table th,.asp-analysis-table td{"
            "border:1px solid #c9cdd2;padding:6px 8px;text-align:left;vertical-align:top;"
            "white-space:normal;word-break:break-word;}"
            ".asp-analysis-table th{background:#f3f5f7;font-weight:600;position:sticky;top:0;}"
            ".asp-analysis-table tr:nth-child(even) td{background:#fafafa;}"
            "</style>"
            "<div style='max-height:430px;overflow:auto;border:1px solid #c9cdd2;'>"
            + table_html
            + "</div>"
        )

    def _styled_dataframe(self, df, precision=4):
        """Pandas Styler used by notebook analysis outputs with clear cell borders."""
        if df is None:
            return pd.DataFrame()
        return (
            df.style
            .format(precision=precision, na_rep="—")
            .set_table_styles([
                {"selector": "table", "props": [("border-collapse", "collapse"), ("width", "100%")]},
                {"selector": "th", "props": [("border", "1px solid #c9cdd2"), ("padding", "6px 8px"), ("background", "#f3f5f7"), ("text-align", "left")]},
                {"selector": "td", "props": [("border", "1px solid #c9cdd2"), ("padding", "6px 8px"), ("vertical-align", "top"), ("white-space", "normal"), ("word-break", "break-word")]},
            ])
        )

    def _on_stage2(self, _):
        self.stage2_summary.value = ""
        self.stage2_tables_box.children = ()
        self.run_stage2.disabled = True
        try:
            settings = self._build_settings()
            self._runtime_begin("metadata_geometry", settings)
            result = run_metadata_geometry(settings=settings, iou_threshold=float(self.iou_threshold.value), custom_pairs_text=self.custom_pairs.value)
            self._runtime_finish("metadata_geometry", settings)
            self.last_metadata = result
            self._remember_project(settings)
            self._refresh_resume_project_status(settings)
            tab_children = []
            tab_titles = []
            if result.get('assignment') is not None and len(result.get('assignment')):
                tab_children.append(self.widgets.HTML(self._dataframe_html(result['assignment'])))
                tab_titles.append('View assignment')
            tab_children.extend([
                self.widgets.HTML(self._dataframe_html(result['overlap'])),
                self.widgets.HTML(self._dataframe_html(result['metadata'])),
                self.widgets.HTML(self._dataframe_html(result['geometry'])),
            ])
            tab_titles.extend(['Overlap pairs', 'Image metadata', 'Stereo geometry'])
            if hasattr(self, 'camera_comparison_panel'):
                tab_children.append(self.camera_comparison_panel)
                tab_titles.append('Compared geometry')
            tabs = self.widgets.Tab(children=tab_children)
            for idx, title in enumerate(tab_titles):
                tabs.set_title(idx, title)
            self.stage2_tables_box.children = (tabs,)

            assignment_text = ''
            if result.get('assignment') is not None and len(result.get('assignment')):
                assignment_text = (
                    '<br><b>Normalized tri-stereo convention:</b> '
                    '<code>A = Forward (F), B = Middle / near-nadir (M), C = Backward (B)</code>'
                    '<br><b>Assignment table:</b> <code>'
                    + html.escape(str(result.get('assignment_csv'))) + '</code>'
                )

            self.stage2_summary_details.selected_index = None
            self.stage2_summary.value = (
                "<div style='margin:10px 0;padding:10px;border-left:4px solid #2e7d32;background:#f4fbf4;'>"
                "<b>✓ Metadata and geometry completed.</b><br><b>Prepared images analyzed:</b> <code>"
                + html.escape(str(result['work_dir'])) + "</code>"
                + assignment_text
                + "<br><b>Saved tables:</b> <code>" + html.escape(str(settings.metadata_dir))
                + "</code><br><b>Detailed log:</b> <code>" + html.escape(str(result['log_path']))
                + "</code></div>"
            )
        except Exception as exc:
            try:
                settings = self._build_settings()
                log_path = settings.log_dir / 'metadata_geometry.log'
            except Exception:
                log_path = Path('(log path unavailable)')
            self.stage2_summary_details.selected_index = 0
            self.stage2_summary.value = ("<div style='margin:10px 0;padding:10px;border-left:4px solid #b00020;background:#fff4f4;'><b>✗ Metadata and geometry stopped.</b><br>" + html.escape(type(exc).__name__ + ': ' + str(exc)) + "<br><br><b>Detailed log:</b> <code>" + html.escape(str(log_path)) + "</code></div>")
        finally:
            self.run_stage2.disabled = False

    def display(self):
        self.display_fn(self.container)
        return self


def project_setup():
    return ProjectSetupUI().display()


# =====================================================================
# v0.5.1 CONTINUATION
# PRE-PROCESSING -> POINT CLOUD -> FINAL DSM
#
# IMPORTANT:
# The v0.5 Prepare data + Metadata and geometry implementation above
# is intentionally left unchanged.  The code below only appends the
# remaining tested workflow stages.
# =====================================================================

import shlex
import re
from dataclasses import dataclass
from matplotlib.colors import LightSource


# ============================================================
# PROCESSING SETTINGS
# ============================================================

@dataclass
class PreProcessingSettings:
    alignment_dem: str
    mapproject_dem: str

    # Camera/session model propagated through every camera-dependent ASP stage.
    # RPC is the tested/reproducibility default.
    camera_model: str = "rpc"

    target_epsg: int = 32632
    raw_resolution_m: float = 0.5
    preliminary_pair: str = "AC"

    # Exact defaults from the tested preprocessing notebook.
    # ASP bundle_adjust supports: Cauchy, PseudoHuber, Huber, L1, L2.
    ba_cost_function: Optional[str] = "Cauchy"
    ba_robust_threshold: Optional[float] = 2.0
    ba_max_iterations: Optional[int] = 500

    prelim_stereo_algorithm: str = "asp_bm"
    prelim_xcorr_threshold: float = 2.0
    prelim_cost_mode: int = 2
    prelim_corr_kernel: int = 35
    prelim_subpixel_kernel: int = 45
    prelim_subpixel_mode: int = 2

    corr_memory_limit_mb: int = 10240
    corr_tile_size: int = 3200

    prelim_dem_resolution_m: float = 1.0
    prelim_dem_nodata: float = -9999.0

    pc_align_max_displacement_m: float = 250.0
    pc_align_iterations: int = 100

    aligned_ba_threads: int = 18
    mapproject_threads: int = 18


def _clean_optional_string(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _parse_optional_float(value, field_name: str) -> Optional[float]:
    text = _clean_optional_string(value)
    if text is None:
        return None
    try:
        return float(text)
    except Exception as exc:
        raise ValueError(f"{field_name} must be a number or blank.") from exc


def _parse_optional_int(value, field_name: str) -> Optional[int]:
    text = _clean_optional_string(value)
    if text is None:
        return None
    try:
        return int(float(text))
    except Exception as exc:
        raise ValueError(f"{field_name} must be an integer or blank.") from exc


@dataclass
class FinalProcessingSettings:
    stereo_mode: str = "dual"

    # Selected internal ASP view tags.
    single_pairs: Tuple[str, ...] = ("AC",)
    dual_configurations: Tuple[str, ...] = ("CAB",)

    # Presets: BM / SGM / MGM.
    # A custom ASP --stereo-algorithm value can also be entered.
    algorithm_tag: str = "MGM"
    custom_cost_mode: int = 4

    # Preset and/or user-entered linked CK:SK pairs.
    kernel_pairs: Tuple[Tuple[int, int], ...] = ((9, 21),)

    # Exact defaults used by the final reconstruction notebook.
    xcorr_threshold: float = 2.0
    corr_memory_limit_mb: int = 10240
    corr_tile_size: int = 3200
    subpixel_mode: int = 2

    pc_merge_threads: int = 18

    final_dsm_resolution_m: float = 1.0
    max_valid_triangulation_error_m: float = 1.0
    final_dsm_threads: int = 0
    final_dsm_nodata: float = -9999.0
    final_dsm_compression: str = "Deflate"
    create_error_image: bool = True


FINAL_ALGORITHMS = {
    "BM": {
        "asp_algorithm": "asp_bm",
        "cost_mode": 2,
        "kernel_pairs": (
            (5, 9),
            (7, 15),
            (9, 21),
            (15, 25),
            (25, 35),
            (35, 45),
        ),
    },
    "SGM": {
        "asp_algorithm": "asp_sgm",
        "cost_mode": 4,
        "kernel_pairs": (
            (5, 9),
            (7, 15),
            (9, 21),
        ),
    },
    "MGM": {
        "asp_algorithm": "asp_mgm",
        "cost_mode": 4,
        "kernel_pairs": (
            (5, 9),
            (7, 15),
            (9, 21),
        ),
    },
}


def _safe_algorithm_filename(value: str) -> str:
    """Create a filesystem-safe algorithm tag without changing the ASP value."""
    value = str(value).strip()
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    return safe or "custom_algorithm"


def _resolve_final_algorithm(final: FinalProcessingSettings):
    """
    Resolve tested algorithm presets or an explicitly entered custom
    ASP --stereo-algorithm value.

    Preset cost modes stay automatic:
        BM  -> 2
        SGM -> 4
        MGM -> 4

    For CUSTOM, the user-entered algorithm and cost mode are used.
    """
    raw = str(final.algorithm_tag).strip()

    if not raw:
        raise ValueError("Stereo algorithm cannot be empty.")

    preset = FINAL_ALGORITHMS.get(raw.upper())

    if preset is not None:
        return {
            "display_name": raw.upper(),
            "asp_algorithm": preset["asp_algorithm"],
            "cost_mode": preset["cost_mode"],
            "preset": True,
        }

    return {
        "display_name": raw,
        "asp_algorithm": raw,
        "cost_mode": int(final.custom_cost_mode),
        "preset": False,
    }


def _parse_custom_kernel_pairs(text: str) -> Tuple[Tuple[int, int], ...]:
    """
    Parse additional linked CK:SK pairs.

    Accepted examples:
        11:23
        11:23,13:27
        11:23 13:27
    """
    text = str(text or "").strip()
    if not text:
        return ()

    pairs = []
    for token in re.split(r"[,\s;]+", text):
        token = token.strip()
        if not token:
            continue

        if ":" not in token:
            raise ValueError(
                f"Invalid custom kernel pair '{token}'. "
                "Use CK:SK, for example 11:23."
            )

        ck_text, sk_text = token.split(":", 1)

        try:
            ck = int(ck_text)
            sk = int(sk_text)
        except Exception as exc:
            raise ValueError(
                f"Invalid custom kernel pair '{token}'. "
                "Both CK and SK must be integers."
            ) from exc

        if ck <= 0 or sk <= 0:
            raise ValueError(
                f"Invalid custom kernel pair '{token}'. "
                "CK and SK must be positive."
            )

        pairs.append((ck, sk))

    # Remove duplicates while preserving order.
    unique = []
    for pair in pairs:
        if pair not in unique:
            unique.append(pair)

    return tuple(unique)


def _raster_basic_summary(path: Path) -> dict:
    path = Path(path)
    with rasterio.open(path) as src:
        return {
            "File": str(path),
            "Width": int(src.width),
            "Height": int(src.height),
            "Bands": int(src.count),
            "CRS": src.crs.to_string() if src.crs else "",
            "Pixel X": abs(float(src.transform.a)),
            "Pixel Y": abs(float(src.transform.e)),
            "NoData": src.nodata,
        }


def _read_transform_matrix(transform_path: Path):
    transform_path = Path(transform_path)
    try:
        matrix = np.loadtxt(transform_path, dtype=float)
        if matrix.shape == (4, 4):
            return matrix
    except Exception:
        pass
    return None


def _parse_pc_align_original_results(
    log_path: Path,
    transform_path: Path,
):
    """
    Extract the important lines that the original pc_align notebook printed.
    The complete console output remains in the ASP log.
    """
    log_path = Path(log_path)
    text = log_path.read_text(
        encoding="utf-8",
        errors="replace",
    )

    labels = [
        "Centroid of source points (Cartesian, meters)",
        "Centroid of source points (lat,lon,z)",
        "Translation vector (Cartesian, meters)",
        "Translation vector (North-East-Down, meters)",
        "Translation vector magnitude (meters)",
        (
            "Maximum displacement of points between the source cloud "
            "with any initial transform applied to it and the source "
            "cloud after alignment to the reference"
        ),
        "Translation vector (lat,lon,z)",
        "Transform scale - 1",
        "Euler angles (degrees)",
        "Euler angles (North-East-Down, degrees)",
        "Axis of rotation and angle (degrees)",
    ]

    extracted = []
    lines = text.splitlines()

    for label in labels:
        for line in reversed(lines):
            stripped = line.strip()
            if stripped.startswith(label):
                extracted.append(stripped)
                break

    matrix = _read_transform_matrix(
        transform_path
    )

    return {
        "important_lines": extracted,
        "matrix": matrix,
        "transform_path": Path(transform_path),
        "log_path": log_path,
    }


def _camera_adjustment_table(
    settings: ProjectSettings,
    paths: dict,
):
    rows = []

    resolved = _adjustment_files(
        paths["aligned_ba_prefix"],
        paths["images"],
        settings.image_names,
        paths.get("cameras"),
    )

    for view, adjustment in zip(settings.image_names, resolved):
        rows.append(
            {
                "Image": view,
                "Status": "Created" if Path(adjustment).is_file() else "Missing",
                "Aligned adjustment / state": str(adjustment),
            }
        )

    return pd.DataFrame(rows)


def _mapproject_output_table(
    settings: ProjectSettings,
    paths: dict,
):
    rows = []

    for view in settings.image_names:
        path = paths["mapprojected"][view]
        info = _raster_basic_summary(path)

        rows.append(
            {
                "Image": view,
                "Output": str(path),
                "Size": f"{info['Width']} × {info['Height']}",
                "CRS": info["CRS"],
                "Pixel size": (
                    f"{info['Pixel X']:g} × {info['Pixel Y']:g}"
                ),
            }
        )

    return pd.DataFrame(rows)


GEOMETRY_LABELS = {
    "AB": "FM — Forward–Middle",
    "AC": "FB — Forward–Backward",
    "BC": "MB — Middle–Backward",
    "ABC": "FMB — Forward–Middle–Backward",
    "BAC": "MFB — Middle–Forward–Backward",
    "CAB": "BFM — Backward–Forward–Middle",
    "ABACBC": "FMFBMB — merged AB + AC + BC",
}


CAMERA_MODEL_CHOICES = {
    "rpc": {
        "label": "RPC — RPC XML (default)",
        "session": "rpc",
        "file_pattern": "RPC_A.XML / RPC_B.XML / RPC_C.XML",
    },
    "pleiades": {
        "label": "Pléiades exact linescan — DIM XML",
        "session": "pleiades",
        "file_pattern": "DIM_A.XML / DIM_B.XML / DIM_C.XML",
    },
}


def _normalize_camera_model(value: str) -> str:
    model = str(value or "rpc").strip().lower()
    if model not in CAMERA_MODEL_CHOICES:
        raise ValueError(
            f"Unsupported camera model: {value!r}. "
            "Choose RPC or Pléiades exact linescan (DIM XML)."
        )
    return model


def _camera_model_label(value: str) -> str:
    return CAMERA_MODEL_CHOICES[_normalize_camera_model(value)]["label"]


def _camera_model_session(settings: ProjectSettings, processing: PreProcessingSettings) -> str:
    model = _normalize_camera_model(processing.camera_model)
    if model == "rpc":
        return "rpc"
    platform = str(settings.platform or "").strip().upper()
    if not (platform.startswith("PHR") or platform.startswith("PNEO")):
        raise ValueError(
            "Pléiades exact linescan mode (-t pleiades) is intended for "
            "Pléiades 1A/1B and Pléiades Neo DIM XML cameras. With the "
            "reproducibility default ASP 3.3.0, keep SPOT 6/7 on the RPC "
            "camera model."
        )
    return "pleiades"


def _camera_model_suffix(processing: PreProcessingSettings) -> str:
    """Preserve all legacy RPC output names; namespace exact-camera outputs."""
    return "" if _normalize_camera_model(processing.camera_model) == "rpc" else "_pleiades"


def _active_dim_inputs(settings: ProjectSettings):
    if settings.crop_enabled:
        dims = {
            view: settings.cropped_dir / f"DIM_{view}_crop.XML"
            for view in settings.image_names
        }
    else:
        dims = {
            view: settings.merged_dir / f"DIM_{view}.XML"
            for view in settings.image_names
        }
    _require_existing_files(*dims.values())
    return dims


def _select_processing_cameras(settings, processing, rpcs):
    model = _normalize_camera_model(processing.camera_model)
    session = _camera_model_session(settings, processing)
    if model == "rpc":
        cameras = rpcs
    else:
        if settings.crop_enabled:
            raise ValueError(
                "Pléiades exact linescan (DIM XML) mode requires full prepared "
                "images in this workflow. Disable AOI image cropping and run "
                "Prepare data again. The AOI crop routine updates RPC offsets "
                "but does not rewrite the exact DIM linescan camera model."
            )
        cameras = _active_dim_inputs(settings)
    _require_existing_files(*cameras.values())
    return cameras, session, model


# ============================================================
# PROCESSING PATHS
# ============================================================

def _processing_root(settings: ProjectSettings) -> Path:
    return settings.project_dir / (
        "cropped_data"
        if settings.crop_enabled
        else "full_data"
    )


def _processing_log_dir(settings: ProjectSettings) -> Path:
    return _processing_root(settings) / "asp_logs"


def _processing_output_dir(settings: ProjectSettings) -> Path:
    return _processing_root(settings) / "asp_out"


def _active_image_inputs(settings: ProjectSettings):
    """
    Reuse exactly the image selection already made by Prepare data.

    Crop OFF:
        merged_tiles/A.tif, B.tif, C.tif
        merged_tiles/RPC_A.XML, RPC_B.XML, RPC_C.XML
        (legacy A.XML/B.XML/C.XML are still accepted)

    Crop ON:
        merged_tiles/cropped_images/A_crop.tif, ...
        merged_tiles/cropped_images/RPC_A_crop.XML, ...
        (legacy A_crop.XML/... are still accepted)
    """
    if settings.crop_enabled:
        images = {
            view: settings.cropped_dir / f"{view}_crop.tif"
            for view in settings.image_names
        }
        rpcs = {
            view: _prepared_rpc_path(settings.cropped_dir, f"{view}_crop")
            for view in settings.image_names
        }
    else:
        images = {
            view: settings.merged_dir / f"{view}.tif"
            for view in settings.image_names
        }
        rpcs = {
            view: _prepared_rpc_path(settings.merged_dir, view)
            for view in settings.image_names
        }

    _require_existing_files(*images.values(), *rpcs.values())
    return images, rpcs


def _dem_resolution_label(dem_path: Path) -> str:
    """
    Reproduce names such as baL50 from the tested map-projection workflow.
    """
    try:
        with rasterio.open(dem_path) as src:
            resolution = abs(float(src.transform.a))

        if resolution >= 1:
            text = str(int(round(resolution)))
        else:
            text = f"{resolution:g}".replace(".", "p")

        return f"L{text}"

    except Exception:
        return "DEM"


def _preprocessing_paths(
    settings: ProjectSettings,
    processing: PreProcessingSettings,
):
    images, rpcs = _active_image_inputs(settings)
    cameras, session_type, camera_model = _select_processing_cameras(
        settings, processing, rpcs
    )
    model_suffix = _camera_model_suffix(processing)

    processing_root = _processing_root(settings)
    log_dir = _processing_log_dir(settings)
    asp_out = _processing_output_dir(settings)

    log_dir.mkdir(parents=True, exist_ok=True)
    asp_out.mkdir(parents=True, exist_ok=True)

    view_tag = "".join(settings.image_names)

    pair = processing.preliminary_pair.upper()

    if len(pair) != 2:
        raise ValueError("The preliminary pair must contain two views.")

    if any(view not in settings.image_names for view in pair):
        raise ValueError(
            f"Preliminary pair {pair} is not valid for "
            f"{settings.image_names}."
        )

    ba_prefix = (
        asp_out
        / f"ba{model_suffix}_{view_tag}"
        / view_tag
    )

    prelim_stereo_dir = (
        asp_out
        / "dems"
        / f"stereo_preliminary_{pair}{model_suffix}"
    )

    prelim_prefix = prelim_stereo_dir / "preliminary"
    prelim_point_cloud = Path(f"{prelim_prefix}-PC.tif")

    prelim_dem_prefix = (
        asp_out
        / "dems"
        / f"preliminary_{pair}{model_suffix}"
    )
    prelim_dem = Path(f"{prelim_dem_prefix}-DEM.tif")

    align_prefix = (
        asp_out
        / "dems"
        / "align"
        / f"preliminary_{pair}_to_LiDAR{model_suffix}"
    )
    align_transform = Path(f"{align_prefix}-transform.txt")

    aligned_ba_prefix = (
        asp_out
        / f"ba_aligned{model_suffix}_{view_tag}"
        / view_tag
    )

    mapproject_dir = asp_out / "mapproject"

    map_label = _dem_resolution_label(
        Path(processing.mapproject_dem)
    )

    mapprojected = {
        view: (
            mapproject_dir
            / (
                f"{view}_{settings.project_name}_"
                f"{processing.raw_resolution_m:g}m_"
                f"ba{map_label}{model_suffix}.tif"
            )
        )
        for view in settings.image_names
    }

    return {
        "processing_root": processing_root,
        "log_dir": log_dir,
        "asp_out": asp_out,
        "images": images,
        "rpcs": rpcs,
        "cameras": cameras,
        "camera_model": camera_model,
        "session_type": session_type,
        "model_suffix": model_suffix,
        "view_tag": view_tag,
        "pair": pair,
        "ba_prefix": ba_prefix,
        "ba_log": log_dir / f"bundle_adjust.{view_tag}{model_suffix}.log",
        "prelim_stereo_dir": prelim_stereo_dir,
        "prelim_prefix": prelim_prefix,
        "prelim_point_cloud": prelim_point_cloud,
        "prelim_log": log_dir / f"stereo_preliminary.{pair}{model_suffix}.log",
        "prelim_dem_prefix": prelim_dem_prefix,
        "prelim_dem": prelim_dem,
        "prelim_dem_log": log_dir / f"point2dem.preliminary_{pair}{model_suffix}.log",
        "align_prefix": align_prefix,
        "align_transform": align_transform,
        "align_log": log_dir / f"pc_align.preliminary_{pair}_to_LiDAR{model_suffix}.log",
        "aligned_ba_prefix": aligned_ba_prefix,
        "aligned_ba_log": log_dir / f"bundle_adjust.aligned_{view_tag}{model_suffix}.log",
        "mapproject_dir": mapproject_dir,
        "mapprojected": mapprojected,
        "mapproject_logs": {
            view: log_dir / f"mapproject.{view}{model_suffix}.log"
            for view in settings.image_names
        },
    }


def _save_processing_state(
    settings: ProjectSettings,
    section_name: str,
    payload: dict,
):
    """
    Save interface choices/results without changing the original v0.5
    project_settings.json format.
    """
    path = settings.project_dir / "processing_state.json"

    if path.exists():
        try:
            state = json.loads(
                path.read_text(encoding="utf-8")
            )
        except Exception:
            state = {}
    else:
        state = {}

    state[section_name] = payload

    path.write_text(
        json.dumps(state, indent=2),
        encoding="utf-8",
    )

    return path


# ============================================================
# COMMAND EXECUTION
# ============================================================

def _require_existing_files(*paths):
    missing = [
        str(Path(path))
        for path in paths
        if not Path(path).is_file()
    ]

    if missing:
        raise FileNotFoundError(
            "Required file(s) not found:\n"
            + "\n".join(missing)
        )


def _remove_path(path: Path):
    path = Path(path)

    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _prepare_output_prefix(
    prefix: Path,
    log_file: Path,
    overwrite: bool,
):
    """
    Python equivalent of the original prepare_output_prefix helper.
    """
    prefix = Path(prefix)
    log_file = Path(log_file)

    prefix.parent.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # ASP prefixes may legitimately contain dots, for example the final
    # DSM prefix ending in "-1.0m".  Therefore do not use Path.suffix to
    # decide whether this is a file or a prefix.  Always remove everything
    # beginning with the requested ASP prefix, matching the original Bash
    # prepare_output_prefix behavior.
    matches = list(
        prefix.parent.glob(prefix.name + "*")
    )

    conflict = bool(matches) or log_file.exists()

    if not conflict:
        return

    if not overwrite:
        raise FileExistsError(
            "Outputs already exist for:\n"
            f"{prefix}\n\n"
            "Enable 'Overwrite existing outputs' to replace them."
        )

    for item in matches:
        _remove_path(item)

    if log_file.exists():
        log_file.unlink()


def _prepare_run_directory(
    run_directory: Path,
    log_file: Path,
    overwrite: bool,
):
    """
    Python equivalent of the original prepare_stereo_run helper.
    """
    run_directory = Path(run_directory)
    log_file = Path(log_file)

    if run_directory.exists() or log_file.exists():
        if not overwrite:
            raise FileExistsError(
                "Outputs already exist:\n"
                f"{run_directory}\n\n"
                "Enable 'Overwrite existing outputs' to replace them."
            )

        _remove_path(run_directory)

        if log_file.exists():
            log_file.unlink()

    run_directory.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)


def _execute_managed_process(
    cmd,
    *,
    cwd=None,
    env=None,
    stdout=None,
    stderr=None,
    text=True,
    command_label="",
):
    """Run one external command under the current notebook execution controller."""
    controller = _current_process_controller()
    if controller is not None:
        controller.check_cancelled()
        controller.wait_if_paused()

    process = subprocess.Popen(
        [str(value) for value in cmd],
        cwd=None if cwd is None else str(cwd),
        env=env,
        stdout=stdout,
        stderr=stderr,
        text=text,
        **_popen_kwargs_for_managed_process(),
    )

    if controller is not None:
        controller.attach_process(process, command_label=command_label or str(cmd[0]))

    try:
        returncode = process.wait()
    finally:
        if controller is not None:
            controller.detach_process(process)

    if controller is not None and controller.cancelled:
        raise WorkflowCancelled(
            f"Run stopped by user while executing {command_label or cmd[0]}."
        )

    return returncode


def _looks_like_workflow_cli_wrapper(executable: str | Path) -> bool:
    """Return True when PATH resolves to this package's Python console wrapper.

    Managed execution controls should attach to the real ASP executable when
    possible, rather than to a short-lived Python launcher that then starts ASP.
    """
    path = Path(executable)
    try:
        if path.suffix.lower() == ".exe":
            return False
        text = path.read_text(encoding="utf-8", errors="ignore")[:8192]
        return "pleiades_asp_runner.cli" in text
    except Exception:
        return False


def _managed_asp_executable(command: str):
    """Return a directly executable managed ASP binary when already installed."""
    try:
        from pleiades_asp_runner.installer import asp_bin_location, is_windows
        if is_windows():
            return None
        candidate = Path(asp_bin_location()) / command
        if candidate.is_file():
            return candidate
    except Exception:
        return None
    return None


def _run_asp_command(
    command: str,
    arguments: Sequence,
    log_file: Path,
    cwd: Path,
):
    """
    Execute ASP from Python and write the long console output to a log.

    Long-running commands are launched with ``Popen`` inside a managed process
    group. When the notebook UI is running them in its background worker this
    enables real Pause / Resume / Stop controls, including child ASP processes.

    The installed workflow wrappers are used when available. For ASP tools
    that are not exposed as wrappers, the function falls back to the ASP
    installation managed by ``pleiades_asp_runner``.
    """
    log_file = Path(log_file)
    cwd = Path(cwd)

    log_file.parent.mkdir(parents=True, exist_ok=True)
    cwd.mkdir(parents=True, exist_ok=True)

    arguments = list(arguments)
    controller = _current_process_controller()

    if controller is not None:
        controller.check_cancelled()
        controller.wait_if_paused()

    with log_file.open("w", encoding="utf-8") as stream:
        stream.write(f"Command: {command}\n")
        stream.write("Arguments:\n")
        for value in arguments:
            stream.write(f"  {value}\n")
        stream.write("\n" + "=" * 80 + "\n")
        stream.flush()

        executable = shutil.which(command)
        env = None

        # When PATH points to the package console-script wrapper, bypass it and
        # attach the controller directly to the real ASP executable. This makes
        # Pause/Resume/Stop act on ASP itself and avoids launcher-only PIDs.
        managed_direct = _managed_asp_executable(command)
        if executable is not None and _looks_like_workflow_cli_wrapper(executable) and managed_direct is not None:
            executable = str(managed_direct)

        if executable is not None:
            cmd = [
                executable,
                *[str(value) for value in arguments],
            ]
            process_cwd = cwd

        else:
            try:
                from pleiades_asp_runner.installer import (
                    asp_bin_location,
                    is_windows,
                    _wsl_base,
                    wsl_execution_environment,
                )
                from pleiades_asp_runner.bridge import (
                    host_to_runtime_path,
                )

            except Exception as exc:
                raise RuntimeError(
                    f"ASP command '{command}' was not found on PATH "
                    "and the managed ASP installation could not be accessed."
                ) from exc

            asp_bin = asp_bin_location()

            if is_windows():
                runtime_cwd = host_to_runtime_path(cwd)

                runtime_args = []
                for value in arguments:
                    if isinstance(value, Path):
                        runtime_args.append(host_to_runtime_path(value))
                    else:
                        runtime_args.append(str(value))

                env_args = [
                    f"{key}={value}"
                    for key, value in wsl_execution_environment().items()
                ]

                cmd = [
                    *_wsl_base(),
                    "--cd",
                    runtime_cwd,
                    "--",
                    "env",
                    *env_args,
                    f"{asp_bin}/{command}",
                    *runtime_args,
                ]
                process_cwd = None

            else:
                managed_executable = Path(asp_bin) / command

                if not managed_executable.is_file():
                    raise FileNotFoundError(
                        f"ASP executable not found:\n{managed_executable}"
                    )

                env = os.environ.copy()
                env["PATH"] = (
                    str(Path(asp_bin))
                    + os.pathsep
                    + env.get("PATH", "")
                )

                cmd = [
                    str(managed_executable),
                    *[str(value) for value in arguments],
                ]
                process_cwd = cwd

        stream.write("Resolved command:\n  " + " ".join(str(v) for v in cmd) + "\n")
        stream.flush()

        try:
            returncode = _execute_managed_process(
                cmd,
                cwd=process_cwd,
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                text=True,
                command_label=command,
            )
        except WorkflowCancelled:
            stream.write("\n[workflow] Run stopped by user.\n")
            stream.flush()
            raise WorkflowCancelled(
                f"Run stopped by user while executing {command}. "
                f"Partial outputs, if any, were left on disk. Detailed log: {log_file}"
            )

        if returncode != 0:
            try:
                lines = log_file.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
                tail = "\n".join(lines[-35:])
            except Exception:
                tail = ""
            message = (
                f"{command} stopped with exit code {returncode}.\n"
                f"Detailed log: {log_file}"
            )
            if tail:
                message += "\n\nLast ASP log lines:\n" + tail
            raise RuntimeError(message)

        if controller is not None:
            controller.check_cancelled()


def _extract_cam_test_triplet(pattern: str, text: str):
    """Return cam_test Min/Median/Max triplet, or NaNs when unavailable."""
    match = re.search(pattern, text, flags=re.MULTILINE | re.IGNORECASE)
    if not match:
        return (np.nan, np.nan, np.nan)
    return tuple(float(match.group(i)) for i in range(1, 4))


def _parse_cam_test_metrics(log_path: Path) -> dict:
    """Parse the numerical camera-comparison diagnostics printed by ASP cam_test."""
    path = Path(log_path)
    output = path.read_text(encoding="utf-8", errors="replace")

    number = r"([0-9eE+\-.]+)"

    direction = _extract_cam_test_triplet(
        rf"cam1\s+to\s+cam2\s+camera\s+direction\s+diff\s+norm\s+"
        rf"Min:\s*{number}\s+Median:\s*{number}\s+Max:\s*{number}",
        output,
    )
    dim_to_rpc = _extract_cam_test_triplet(
        rf"cam1\s+to\s+cam2\s+pixel\s+diff\s+"
        rf"Min:\s*{number}\s+Median:\s*{number}\s+Max:\s*{number}",
        output,
    )
    rpc_to_dim = _extract_cam_test_triplet(
        rf"cam2\s+to\s+cam1\s+pixel\s+diff\s+"
        rf"Min:\s*{number}\s+Median:\s*{number}\s+Max:\s*{number}",
        output,
    )

    sample_match = re.search(
        r"Number\s+of\s+samples\s+used:\s*(\d+)",
        output,
        flags=re.IGNORECASE,
    )
    elapsed_match = re.search(
        rf"Elapsed\s+time\s+per\s+sample:\s*{number}\s+milliseconds",
        output,
        flags=re.IGNORECASE,
    )

    return {
        "Samples": int(sample_match.group(1)) if sample_match else np.nan,
        "Direction_Min": direction[0],
        "Direction_Median": direction[1],
        "Direction_Max": direction[2],
        "DIM_to_RPC_Min_px": dim_to_rpc[0],
        "DIM_to_RPC_Median_px": dim_to_rpc[1],
        "DIM_to_RPC_Max_px": dim_to_rpc[2],
        "RPC_to_DIM_Min_px": rpc_to_dim[0],
        "RPC_to_DIM_Median_px": rpc_to_dim[1],
        "RPC_to_DIM_Max_px": rpc_to_dim[2],
        "Elapsed_ms_per_sample": (
            float(elapsed_match.group(1)) if elapsed_match else np.nan
        ),
    }


def _plot_camera_model_comparison(settings: ProjectSettings, table: pd.DataFrame):
    """Plot DIM→RPC minimum, median, and maximum pixel discrepancy for the active views."""
    numeric = table.copy()
    numeric = numeric[pd.to_numeric(numeric["DIM_to_RPC_Median_px"], errors="coerce").notna()]
    if numeric.empty:
        return None

    labels = [
        f"{str(row.Dataset).replace('_', ' ')} - {row.View}"
        for row in numeric.itertuples(index=False)
    ]
    x = np.arange(len(numeric))
    minv = pd.to_numeric(numeric["DIM_to_RPC_Min_px"], errors="coerce").to_numpy(float)
    med = pd.to_numeric(numeric["DIM_to_RPC_Median_px"], errors="coerce").to_numpy(float)
    maxv = pd.to_numeric(numeric["DIM_to_RPC_Max_px"], errors="coerce").to_numpy(float)

    fig, ax = plt.subplots(figsize=(13, 6))
    ax.plot(x, minv, marker="^", linewidth=1.2, linestyle=":", label="Minimum")
    ax.plot(x, med, marker="o", linewidth=1.5, label="Median")
    ax.plot(x, maxv, marker="s", linewidth=1.2, linestyle="--", label="Maximum")

    from matplotlib.ticker import ScalarFormatter, MaxNLocator
    ax.yaxis.set_major_locator(MaxNLocator(nbins=8))
    ax.yaxis.set_major_formatter(ScalarFormatter(useMathText=False))
    ax.ticklabel_format(axis="y", style="plain", useOffset=False)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=60, ha="right")
    ax.set_ylabel("DIM–RPC pixel difference (pixels)")
    ax.set_xlabel("Acquisition and view")
    ax.set_title("Comparison of exact line-scan and RPC camera models")
    ax.grid(True, axis="y", alpha=0.25)
    ax.legend(frameon=False)
    fig.tight_layout()

    png = settings.figure_dir / f"{settings.project_name}_DIM_RPC_camera_comparison.png"
    pdf = settings.figure_dir / f"{settings.project_name}_DIM_RPC_camera_comparison.pdf"
    fig.savefig(png, dpi=300, bbox_inches="tight")
    fig.savefig(pdf, format="pdf", bbox_inches="tight")
    return {"figure": fig, "png": png, "pdf": pdf}


def run_camera_model_comparison(settings: ProjectSettings):
    """
    Compare each prepared exact DIM camera against its RPC model using the
    same cam_test principle as the external multi-acquisition analysis code.

    No --height-above-datum or --sample-rate override is added here.  ASP is
    allowed to use its own cam_test defaults, exactly as in the reference
    command supplied by the workflow author.
    """
    create_project_folders(settings)

    if settings.acquisition_mode == "tri_stereo" and not _tri_stereo_normalization_ready(settings):
        raise ValueError(
            "Run Metadata and geometry first so tri-stereo A/B/C are normalized "
            "to Forward/Middle/Backward before comparing DIM and RPC geometry."
        )

    platform = str(settings.platform or "").strip().upper()
    if platform.startswith("PHR") or platform.startswith("PNEO"):
        exact_session = "pleiades"
    elif platform.startswith("SPOT"):
        exact_session = "spot"
    else:
        raise ValueError(
            "DIM vs RPC geometry comparison is available for Pléiades, "
            "Pléiades Neo, and supported SPOT 6/7 exact-camera datasets."
        )

    rows = []
    logs = {}

    for view in settings.image_names:
        image = settings.merged_dir / f"{view}.tif"
        exact_camera = settings.merged_dir / f"DIM_{view}.XML"
        rpc_camera = _prepared_rpc_path(settings.merged_dir, view)
        _require_existing_files(image, exact_camera, rpc_camera)

        log_path = settings.asp_logs_dir / f"cam_test.{view}.rpc_vs_dim.log"
        logs[view] = log_path

        # Intentionally mirrors the supplied external comparison script:
        # cam_test --image <view>.tif --cam1 DIM_<view>.XML --cam2 RPC_<view>.XML
        #          --session1 <exact session> --session2 rpc
        _run_asp_command(
            "cam_test",
            [
                "--image", image,
                "--cam1", exact_camera,
                "--cam2", rpc_camera,
                "--session1", exact_session,
                "--session2", "rpc",
            ],
            log_path,
            settings.project_dir,
        )

        metrics = _parse_cam_test_metrics(log_path)
        rows.append(
            {
                "Dataset": settings.project_name,
                "Sensor": settings.platform,
                "View": view,
                "Status": "OK",
                **metrics,
            }
        )

    # Keep the same column order as the external comparison code.
    ordered_columns = [
        "Dataset", "Sensor", "View", "Status", "Samples",
        "Direction_Min", "Direction_Median", "Direction_Max",
        "DIM_to_RPC_Min_px", "DIM_to_RPC_Median_px", "DIM_to_RPC_Max_px",
        "RPC_to_DIM_Min_px", "RPC_to_DIM_Median_px", "RPC_to_DIM_Max_px",
        "Elapsed_ms_per_sample",
    ]
    table = pd.DataFrame(rows).reindex(columns=ordered_columns)
    csv_path = settings.metadata_dir / f"{settings.project_name}_cam_test_DIM_vs_RPC.csv"
    table.to_csv(csv_path, index=False)
    plot = _plot_camera_model_comparison(settings, table)

    return {
        "table": table,
        "csv": csv_path,
        "logs": logs,
        "log_dir": settings.asp_logs_dir,
        "plot": plot,
        "exact_session": exact_session,
    }


# ============================================================
# BUNDLE-ADJUSTMENT RESIDUALS
# Copied from the existing workflow logic, but receives paths
# directly instead of reading Metadata-Copy1.sh.
# ============================================================

def _parse_residual_statistics(filepath, active_views):
    filepath = Path(filepath)

    if not filepath.is_file():
        raise FileNotFoundError(
            f"Residual-statistics file not found:\n{filepath}"
        )

    records = []
    reading_camera_residuals = False

    with filepath.open(
        "r",
        encoding="utf-8",
        errors="replace",
    ) as stream:

        for raw_line in stream:
            line = raw_line.strip()

            if not line:
                continue

            if line.startswith(
                "Mean and median norm of residual error"
            ):
                reading_camera_residuals = True
                continue

            if line.startswith(
                "Camera weight position and orientation"
            ):
                break

            if not reading_camera_residuals:
                continue

            parts = [
                part.strip()
                for part in line.split(",")
            ]

            if len(parts) < 4:
                continue

            camera_name = Path(parts[0]).name

            match = re.fullmatch(
                r"(?:(?:DIM|RPC)_)?([ABC])(?:_crop)?\.(?:XML|xml|tif|tiff)",
                camera_name,
            )

            if match is None:
                continue

            image_id = match.group(1).upper()

            if image_id not in active_views:
                continue

            records.append(
                {
                    "Image": image_id,
                    "Mean": float(parts[1]),
                    "Median": float(parts[2]),
                    "Count": int(float(parts[3])),
                }
            )

    if not records:
        raise ValueError(
            f"No camera residual rows were found in:\n{filepath}"
        )

    return (
        pd.DataFrame(records)
        .set_index("Image")
        .reindex(active_views)
    )


def _bundle_adjustment_residual_summary(
    ba_prefix: Path,
    active_views,
):
    initial = _parse_residual_statistics(
        f"{ba_prefix}-initial_residuals_stats.txt",
        active_views,
    )

    final = _parse_residual_statistics(
        f"{ba_prefix}-final_residuals_stats.txt",
        active_views,
    )

    summary = pd.DataFrame(
        {
            "Initial mean (px)": initial["Mean"],
            "Final mean (px)": final["Mean"],
            "Initial median (px)": initial["Median"],
            "Final median (px)": final["Median"],
            "Point count": final["Count"],
        }
    )

    summary.index.name = "Image"

    return summary.round(
        {
            "Initial mean (px)": 4,
            "Final mean (px)": 4,
            "Initial median (px)": 4,
            "Final median (px)": 4,
            "Point count": 0,
        }
    )


# ============================================================
# PRE-PROCESSING FIGURES
# ============================================================

def _read_dem_preview(
    raster_path: Path,
    max_display_size=1800,
):
    raster_path = Path(raster_path)

    with rasterio.open(raster_path) as src:
        scale = min(
            1.0,
            max_display_size
            / max(src.width, src.height),
        )

        width = max(
            1,
            int(round(src.width * scale)),
        )
        height = max(
            1,
            int(round(src.height * scale)),
        )

        array = src.read(
            1,
            out_shape=(height, width),
            resampling=Resampling.bilinear,
            masked=True,
        ).astype(np.float32)

        data = np.asarray(
            array.data,
            dtype=np.float32,
        )

        mask = (
            np.ma.getmaskarray(array)
            | ~np.isfinite(data)
        )

        if src.nodata is not None:
            mask |= np.isclose(data, src.nodata)

        array = np.ma.array(
            data,
            mask=mask,
        )

        bounds = src.bounds
        crs = src.crs

    return array, bounds, crs



def _plot_reference_dem_from_path(
    settings: ProjectSettings,
    dem_path: Path,
    role_label: str,
    filename_stem: str,
):
    """Plot the actual prepared reference DEM before any ASP stage starts."""
    dem_path = Path(dem_path)
    dem, bounds, crs = _read_dem_preview(dem_path)
    valid = dem.compressed()
    if valid.size == 0:
        raise ValueError(f"The prepared reference DEM has no valid values:\n{dem_path}")

    p2, p98 = np.percentile(valid, [2, 98])
    actual_min = float(np.min(valid))
    actual_max = float(np.max(valid))
    filled = dem.filled(float(np.median(valid)))
    hillshade = LightSource(azdeg=315, altdeg=45).hillshade(filled, vert_exag=1.0)
    hillshade = np.ma.array(hillshade, mask=np.ma.getmaskarray(dem))
    extent = [bounds.left, bounds.right, bounds.bottom, bounds.top]

    fig, ax = plt.subplots(figsize=(9.2, 7.2))
    image = ax.imshow(
        dem, extent=extent, origin="upper", cmap="terrain", vmin=p2, vmax=p98
    )
    ax.imshow(
        hillshade, extent=extent, origin="upper", cmap="gray", alpha=0.22
    )
    cb = fig.colorbar(image, ax=ax, shrink=0.82, pad=0.025)
    cb.set_label("Elevation (m)")
    ax.set_title(f"{settings.project_name} — {role_label}")
    if crs is not None and crs.is_geographic:
        ax.set_xlabel("Longitude")
        ax.set_ylabel("Latitude")
    else:
        ax.set_xlabel("Easting (m)")
        ax.set_ylabel("Northing (m)")
    ax.set_aspect("equal")
    ax.grid(True, color="white", alpha=0.20, linewidth=0.5, linestyle="--")
    fig.tight_layout()

    settings.figure_dir.mkdir(parents=True, exist_ok=True)
    png = settings.figure_dir / f"{filename_stem}.png"
    pdf = settings.figure_dir / f"{filename_stem}.pdf"
    fig.savefig(png, dpi=200, bbox_inches="tight")
    fig.savefig(pdf, format="pdf", bbox_inches="tight")

    with rasterio.open(dem_path) as src:
        summary = {
            "Path": str(dem_path),
            "CRS": src.crs.to_string() if src.crs else "",
            "Width": int(src.width),
            "Height": int(src.height),
            "Pixel X": abs(float(src.transform.a)),
            "Pixel Y": abs(float(src.transform.e)),
            "Left": float(src.bounds.left),
            "Right": float(src.bounds.right),
            "Bottom": float(src.bounds.bottom),
            "Top": float(src.bounds.top),
            "Elevation min": actual_min,
            "Elevation max": actual_max,
            "NoData": src.nodata,
        }
    return {"figure": fig, "png": png, "pdf": pdf, "summary": summary}



def _plot_reference_geoid_qc(
    settings: ProjectSettings,
    result: dict,
    model_label: str,
):
    """Plot the actual geoid/quasi-geoid undulation raster(s) used for conversion."""
    candidates = [
        (
            "Alignment reference",
            result.get("alignment_n"),
        ),
        (
            "Map-projection reference",
            result.get("map_n"),
        ),
    ]

    rasters = []
    seen = set()
    for role, raw_path in candidates:
        if not raw_path:
            continue
        path = Path(raw_path)
        resolved = str(path.resolve()) if path.exists() else str(path)
        if resolved in seen:
            continue
        seen.add(resolved)
        rasters.append((role, path))

    if not rasters:
        return None

    ncols = len(rasters)
    fig_width = 8.4 if ncols == 1 else 13.6
    fig, axes = plt.subplots(
        1,
        ncols,
        figsize=(fig_width, 6.4),
        squeeze=False,
    )
    axes = axes.ravel()

    summaries = []
    for ax, (role, raster_path) in zip(axes, rasters):
        n_raster, bounds, crs = _read_dem_preview(raster_path)
        valid = n_raster.compressed()
        if valid.size == 0:
            raise ValueError(
                "The prepared geoid/vertical-correction raster has no valid "
                f"values:\n{raster_path}"
            )

        actual_min = float(np.min(valid))
        actual_max = float(np.max(valid))
        p2, p98 = np.percentile(valid, [2, 98])
        if np.isclose(p2, p98):
            p2 = actual_min
            p98 = actual_max
        if np.isclose(p2, p98):
            p2 -= 0.5
            p98 += 0.5

        extent = [
            bounds.left,
            bounds.right,
            bounds.bottom,
            bounds.top,
        ]
        image = ax.imshow(
            n_raster,
            extent=extent,
            origin="upper",
            cmap="viridis",
            vmin=p2,
            vmax=p98,
        )
        cb = fig.colorbar(
            image,
            ax=ax,
            shrink=0.82,
            pad=0.025,
        )
        cb.set_label("Geoid undulation N (m)")
        ax.set_title(role)

        if crs is not None and crs.is_geographic:
            ax.set_xlabel("Longitude")
            ax.set_ylabel("Latitude")
        else:
            ax.set_xlabel("Easting (m)")
            ax.set_ylabel("Northing (m)")

        ax.set_aspect("equal")
        ax.grid(
            True,
            color="white",
            alpha=0.20,
            linewidth=0.5,
            linestyle="--",
        )

        with rasterio.open(raster_path) as src:
            summaries.append(
                {
                    "Role": role,
                    "Path": str(raster_path),
                    "CRS": src.crs.to_string() if src.crs else "",
                    "Width": int(src.width),
                    "Height": int(src.height),
                    "Pixel X": abs(float(src.transform.a)),
                    "Pixel Y": abs(float(src.transform.e)),
                    "Left": float(src.bounds.left),
                    "Right": float(src.bounds.right),
                    "Bottom": float(src.bounds.bottom),
                    "Top": float(src.bounds.top),
                    "N min": actual_min,
                    "N max": actual_max,
                    "N mean": float(np.mean(valid)),
                    "NoData": src.nodata,
                }
            )

    fig.suptitle(
        f"{settings.project_name} — Geoid / vertical correction — {model_label}",
        y=0.995,
    )
    fig.tight_layout()

    settings.figure_dir.mkdir(
        parents=True,
        exist_ok=True,
    )
    png = (
        settings.figure_dir
        / "reference_geoid_preview.png"
    )
    pdf = (
        settings.figure_dir
        / "reference_geoid_preview.pdf"
    )
    fig.savefig(
        png,
        dpi=200,
        bbox_inches="tight",
    )
    fig.savefig(
        pdf,
        dpi=300,
        bbox_inches="tight",
    )

    return {
        "figure": fig,
        "png": png,
        "pdf": pdf,
        "summaries": summaries,
        "model_label": model_label,
    }


def _plot_preliminary_dem_from_path(
    settings: ProjectSettings,
    dem_path: Path,
    pair: str,
):
    dem, bounds, crs = _read_dem_preview(dem_path)

    valid = dem.compressed()

    if valid.size == 0:
        raise ValueError(
            f"The preliminary DEM has no valid values:\n{dem_path}"
        )

    vmin, vmax = np.percentile(valid, [2, 98])

    filled = dem.filled(
        float(np.median(valid))
    )

    hillshade = LightSource(
        azdeg=315,
        altdeg=45,
    ).hillshade(
        filled,
        vert_exag=1.0,
    )

    hillshade = np.ma.array(
        hillshade,
        mask=np.ma.getmaskarray(dem),
    )

    extent = [
        bounds.left,
        bounds.right,
        bounds.bottom,
        bounds.top,
    ]

    fig, ax = plt.subplots(
        figsize=(10, 8)
    )

    plot = ax.imshow(
        dem,
        extent=extent,
        origin="upper",
        cmap="terrain",
        vmin=vmin,
        vmax=vmax,
    )

    ax.imshow(
        hillshade,
        extent=extent,
        origin="upper",
        cmap="gray",
        alpha=0.30,
    )

    colorbar = fig.colorbar(
        plot,
        ax=ax,
        shrink=0.82,
        pad=0.025,
    )
    colorbar.set_label("Elevation (m)")

    ax.set_title(
        f"{settings.project_name} — "
        f"Preliminary {pair} alignment DEM"
    )

    if crs is not None and crs.is_geographic:
        ax.set_xlabel("Longitude")
        ax.set_ylabel("Latitude")
    else:
        ax.set_xlabel("Easting (m)")
        ax.set_ylabel("Northing (m)")

    ax.set_aspect("equal")
    ax.grid(
        True,
        color="white",
        alpha=0.22,
        linewidth=0.5,
        linestyle="--",
    )

    fig.tight_layout()

    settings.figure_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    png = (
        settings.figure_dir
        / f"preliminary_{pair}_alignment_DEM.png"
    )
    pdf = (
        settings.figure_dir
        / f"preliminary_{pair}_alignment_DEM.pdf"
    )

    fig.savefig(
        png,
        dpi=200,
        bbox_inches="tight",
    )
    fig.savefig(
        pdf,
        dpi=300,
        bbox_inches="tight",
    )

    return {
        "figure": fig,
        "png": png,
        "pdf": pdf,
    }


def _read_map_image_preview(
    image_path: Path,
    max_display_size=1600,
):
    image_path = Path(image_path)

    with rasterio.open(image_path) as src:
        scale = min(
            1.0,
            max_display_size
            / max(src.width, src.height),
        )

        width = max(
            1,
            int(round(src.width * scale)),
        )
        height = max(
            1,
            int(round(src.height * scale)),
        )

        image = src.read(
            1,
            out_shape=(height, width),
            resampling=Resampling.bilinear,
            masked=True,
        ).astype(np.float32)

        data = np.asarray(
            image.data,
            dtype=np.float32,
        )

        mask = (
            np.ma.getmaskarray(image)
            | ~np.isfinite(data)
            | np.isclose(data, 0)
        )

        if src.nodata is not None:
            mask |= np.isclose(
                data,
                src.nodata,
            )

        valid = data[~mask]

        if valid.size:
            low, high = np.percentile(
                valid,
                [2, 98],
            )

            if high <= low:
                high = low + 1.0

            data = np.clip(
                (data - low) / (high - low),
                0,
                1,
            )

        array = np.ma.array(
            data,
            mask=mask,
        )

        bounds = src.bounds
        crs = src.crs

    return {
        "array": array,
        "bounds": bounds,
        "crs": crs,
    }


def _plot_mapprojected_from_paths(
    settings: ProjectSettings,
    mapprojected: Dict[str, Path],
    target_epsg: int,
):
    """
    Plot the map-projected images using the common spatial intersection.

    This intentionally reproduces the tested publication/QC behavior from
    the original workflow: only the area shared by all views is displayed.
    The underlying mapproject GeoTIFFs are not cropped or modified.

    Before plotting, every output CRS is checked against the Target CRS
    (EPSG) selected under Advanced pre-processing.
    """
    from matplotlib.ticker import FuncFormatter, MaxNLocator

    prepared = [
        (
            view,
            _read_map_image_preview(
                mapprojected[view]
            ),
        )
        for view in settings.image_names
    ]

    expected_epsg = int(target_epsg)
    for view, item in prepared:
        crs = item["crs"]
        if crs is None:
            raise ValueError(
                "No CRS is defined for map-projected image "
                f"{view}: {mapprojected[view]}"
            )

        output_epsg = crs.to_epsg()
        if output_epsg != expected_epsg:
            raise ValueError(
                "Map-projected image CRS does not match the selected "
                "Target CRS.\n"
                f"Image {view}: {mapprojected[view]}\n"
                f"Image CRS: {crs.to_string()}\n"
                f"Expected: EPSG:{expected_epsg}"
            )

    # Common INTERSECTION, matching the tested original plotting function.
    common_left = max(
        item["bounds"].left
        for _, item in prepared
    )
    common_right = min(
        item["bounds"].right
        for _, item in prepared
    )
    common_bottom = max(
        item["bounds"].bottom
        for _, item in prepared
    )
    common_top = min(
        item["bounds"].top
        for _, item in prepared
    )

    if (
        common_left >= common_right
        or common_bottom >= common_top
    ):
        raise ValueError(
            "The map-projected images do not have a common "
            "projected extent."
        )

    common_extent = [
        common_left,
        common_right,
        common_bottom,
        common_top,
    ]

    fig, axes = plt.subplots(
        1,
        len(prepared),
        figsize=(5.0 * len(prepared), 6.3),
        squeeze=False,
        sharex=True,
        sharey=True,
    )
    axes = axes.ravel()

    cmap = plt.get_cmap("gray").copy()
    cmap.set_bad("white")

    first_crs = prepared[0][1]["crs"]
    is_geographic = (
        first_crs is not None
        and first_crs.is_geographic
    )

    if is_geographic:
        coordinate_formatter = FuncFormatter(
            lambda value, position: f"{value:.4f}"
        )
        common_x_label = "Longitude"
        common_y_label = "Latitude"
    else:
        # Full projected coordinates, no scientific notation or offset.
        coordinate_formatter = FuncFormatter(
            lambda value, position: f"{value:.0f}"
        )
        common_x_label = "Easting (m)"
        common_y_label = "Northing (m)"

    view_labels = _workflow_view_display_labels(settings)

    for ax, (view, item) in zip(
        axes,
        prepared,
    ):
        bounds = item["bounds"]
        extent = [
            bounds.left,
            bounds.right,
            bounds.bottom,
            bounds.top,
        ]

        ax.imshow(
            item["array"],
            extent=extent,
            origin="upper",
            cmap=cmap,
            interpolation="nearest",
        )

        ax.set_xlim(
            common_extent[0],
            common_extent[1],
        )
        ax.set_ylim(
            common_extent[2],
            common_extent[3],
        )
        ax.set_aspect("equal")

        ax.set_title(
            view_labels.get(
                view,
                f"{view} — map-projected",
            )
        )

        ax.grid(
            True,
            color="white",
            alpha=0.22,
            linewidth=0.5,
            linestyle="--",
        )

        ax.xaxis.set_major_locator(
            MaxNLocator(nbins=6)
        )
        ax.yaxis.set_major_locator(
            MaxNLocator(nbins=7)
        )
        ax.xaxis.set_major_formatter(
            coordinate_formatter
        )
        ax.yaxis.set_major_formatter(
            coordinate_formatter
        )
        ax.tick_params(
            axis="x",
            labelrotation=0,
            labelsize=8.5,
        )
        ax.tick_params(
            axis="y",
            labelsize=8.5,
        )
        ax.set_xlabel(common_x_label)

    axes[0].set_ylabel(common_y_label)

    fig.suptitle(
        f"{settings.project_name}: "
        "map-projected image alignment"
    )
    fig.tight_layout(rect=(0.02, 0.02, 1, 0.96))

    settings.figure_dir.mkdir(
        parents=True,
        exist_ok=True,
    )
    png = (
        settings.figure_dir
        / "mapprojected_images_preview.png"
    )
    pdf = (
        settings.figure_dir
        / "mapprojected_images_preview.pdf"
    )

    fig.savefig(
        png,
        dpi=200,
        bbox_inches="tight",
        facecolor="white",
    )
    fig.savefig(
        pdf,
        dpi=300,
        bbox_inches="tight",
        facecolor="white",
    )

    return {
        "figure": fig,
        "png": png,
        "pdf": pdf,
        "target_epsg": expected_epsg,
        "common_extent": common_extent,
    }


# ============================================================
# RUN PRE-PROCESSING
# Exact command sequence from notebook 05.
# ============================================================

def _preflight_exact_pleiades_cameras(settings, paths):
    """Verify that each full image can be loaded with its DIM exact camera.

    This intentionally uses cam_test against the corresponding RPC camera.
    It catches orthorectified/non-camera DIM products, mismatched DIM/image
    pairs, and unsupported exact-camera files before bundle_adjust starts.
    """
    if paths.get("camera_model") != "pleiades":
        return {}

    if settings.crop_enabled:
        raise ValueError(
            "Exact DIM mode requires the full prepared images; disable AOI raw-image cropping."
        )

    logs = {}
    for view in settings.image_names:
        image = paths["images"][view]
        exact = paths["cameras"][view]
        rpc = paths["rpcs"][view]
        log = paths["log_dir"] / f"cam_test.preflight_{view}_DIM_vs_RPC.log"
        _run_asp_command(
            "cam_test",
            [
                "--image", image,
                "--cam1", exact,
                "--cam2", rpc,
                "--session1", "pleiades",
                "--session2", "rpc",
            ],
            log,
            paths["processing_root"],
        )
        logs[view] = log
    return logs


PREPROCESS_STAGE_ORDER = (
    "bundle_adjustment",
    "preliminary_stereo",
    "preliminary_dem",
    "lidar_alignment",
    "camera_transform",
    "map_projection",
)


def _normalized_preprocess_stages(stages=None):
    """Return validated preprocessing stages, preserving workflow order."""
    if stages is None:
        return PREPROCESS_STAGE_ORDER

    if isinstance(stages, str):
        requested = {stages}
    else:
        requested = {str(stage) for stage in stages}

    unknown = requested.difference(PREPROCESS_STAGE_ORDER)
    if unknown:
        raise ValueError(
            "Unknown preprocessing stage(s): "
            + ", ".join(sorted(unknown))
        )

    if not requested:
        raise ValueError("Select at least one preprocessing stage.")

    return tuple(
        stage for stage in PREPROCESS_STAGE_ORDER if stage in requested
    )


def _adjustment_candidates(
    prefix: Path,
    images: dict,
    views: Sequence[str],
    cameras: Optional[dict] = None,
):
    """Return possible ASP adjustment/model-state outputs for each view.

    RPC workflows normally use image-based names (run-A.adjust). Exact
    Pléiades cameras are CSM-backed; depending on ASP version/output mode,
    camera-stem names and adjusted model-state JSON files may also be written.
    """
    candidates = {}
    for view in views:
        image_stem = Path(images[view]).stem
        camera_stem = (
            Path(cameras[view]).stem
            if cameras is not None and view in cameras
            else None
        )
        # ASP names adjustment products from the camera basename for exact
        # Pléiades DIM/CSM cameras (e.g. ABC-DIM_A.adjust).  RPC commonly
        # resolves to the image basename.  Prefer the actual camera stem when
        # it differs, but retain the image-stem fallback for ASP-version
        # compatibility.
        stems = []
        if camera_stem and camera_stem != image_stem:
            stems.append(camera_stem)
        stems.append(image_stem)
        if camera_stem and camera_stem not in stems:
            stems.append(camera_stem)

        paths = []
        for stem in stems:
            paths.extend([
                Path(f"{prefix}-{stem}.adjust"),
                Path(f"{prefix}-{stem}.adjusted_state.json"),
            ])
        candidates[view] = tuple(paths)
    return candidates


def _adjustment_files(
    prefix: Path,
    images: dict,
    views: Sequence[str],
    cameras: Optional[dict] = None,
):
    """Resolve one existing adjustment/state product per view when possible."""
    resolved = []
    for view, options in _adjustment_candidates(prefix, images, views, cameras).items():
        found = next((path for path in options if path.is_file()), None)
        resolved.append(found if found is not None else options[0])
    return tuple(resolved)


def _require_adjustments(
    prefix: Path,
    images: dict,
    views: Sequence[str],
    cameras: Optional[dict] = None,
):
    candidates = _adjustment_candidates(prefix, images, views, cameras)
    resolved = []
    missing = []
    for view, options in candidates.items():
        found = next((path for path in options if path.is_file()), None)
        if found is None:
            missing.append((view, options))
        else:
            resolved.append(found)

    if missing:
        parent = Path(prefix).parent
        produced = sorted(parent.glob(Path(prefix).name + "*")) if parent.is_dir() else []
        expected = []
        for view, options in missing:
            expected.append(
                f"  {view}: " + " OR ".join(str(path) for path in options)
            )
        produced_text = "\n".join(f"  {path.name}" for path in produced) or "  (none)"
        raise FileNotFoundError(
            "Bundle adjustment finished but no recognized adjustment/model-state "
            "product was found for one or more views.\nExpected one of:\n"
            + "\n".join(expected)
            + "\n\nFiles actually produced for this prefix:\n"
            + produced_text
        )
    return tuple(resolved)



def _load_saved_preprocessing_state(settings: ProjectSettings) -> dict:
    """Return the last saved preprocessing state for a project, if available."""
    path = settings.project_dir / "processing_state.json"
    if not path.is_file():
        return {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    pre = state.get("pre_processing", {}) if isinstance(state, dict) else {}
    return pre if isinstance(pre, dict) else {}


def _adjustments_ready(prefix, images, views, cameras=None) -> bool:
    """True when one recognized ASP adjustment/model-state exists per view."""
    try:
        candidates = _adjustment_candidates(Path(prefix), images, views, cameras)
        return all(any(path.is_file() for path in options) for options in candidates.values())
    except Exception:
        return False


def _stage_prerequisites_ready(stage, settings, processing, paths) -> bool:
    """Check only the upstream products required by one preprocessing stage."""
    images = paths["images"]
    cameras = paths["cameras"]
    views = settings.image_names

    if stage == "bundle_adjustment":
        return True
    if stage == "preliminary_stereo":
        return _adjustments_ready(paths["ba_prefix"], images, views, cameras)
    if stage == "preliminary_dem":
        return Path(paths["prelim_point_cloud"]).is_file()
    if stage == "lidar_alignment":
        return (
            Path(paths["prelim_dem"]).is_file()
            and bool(str(processing.alignment_dem).strip())
            and Path(processing.alignment_dem).expanduser().is_file()
        )
    if stage == "camera_transform":
        return (
            Path(paths["align_transform"]).is_file()
            and _adjustments_ready(paths["ba_prefix"], images, views, cameras)
        )
    if stage == "map_projection":
        return (
            bool(str(processing.mapproject_dem).strip())
            and Path(processing.mapproject_dem).expanduser().is_file()
            and _adjustments_ready(paths["aligned_ba_prefix"], images, views, cameras)
        )
    return False


def _processing_variant_from_saved_state(settings, processing):
    """Build a compatible processing configuration from saved project state.

    This is used only as a fallback for *single-stage* resume.  Current UI
    choices are tried first.  If their prerequisite path does not exist, the
    saved camera model / preliminary pair / CRS / reference paths are reused so
    a completed upstream stage can be consumed directly after reopening the
    notebook.
    """
    pre = _load_saved_preprocessing_state(settings)
    if not pre:
        return None

    values = asdict(processing)
    for key in ("camera_model", "target_epsg", "raw_resolution_m", "preliminary_pair"):
        value = pre.get(key)
        if value not in (None, ""):
            values[key] = value

    if not str(values.get("alignment_dem", "")).strip():
        saved = str(pre.get("alignment_dem") or "").strip()
        if saved:
            values["alignment_dem"] = saved
    if not str(values.get("mapproject_dem", "")).strip():
        saved = str(pre.get("mapproject_dem") or "").strip()
        if saved:
            values["mapproject_dem"] = saved

    try:
        values["target_epsg"] = int(values["target_epsg"])
        values["raw_resolution_m"] = float(values["raw_resolution_m"])
        values["preliminary_pair"] = str(values["preliminary_pair"])
        return PreProcessingSettings(**values)
    except Exception:
        return None


def _unique_existing_candidate(candidates):
    existing = []
    seen = set()
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            key = str(path.resolve())
            if key not in seen:
                seen.add(key)
                existing.append(path)
    return existing[0] if len(existing) == 1 else None


def _discover_stage_prerequisites_from_disk(stage, settings, processing, paths):
    """Last-resort discovery for older projects without processing_state.json.

    Only a *unique* matching product is accepted.  If several candidates are
    present the function leaves the expected path unchanged rather than guessing
    which scientific branch the user intended.
    """
    asp_out = Path(paths["asp_out"])
    model_suffix = paths["model_suffix"]
    pair = paths["pair"]
    view_tag = paths["view_tag"]
    images = paths["images"]
    cameras = paths["cameras"]

    if stage in {"preliminary_stereo", "camera_transform"}:
        if not _adjustments_ready(paths["ba_prefix"], images, settings.image_names, cameras):
            dirs = sorted(asp_out.glob(f"ba{model_suffix}_{view_tag}"))
            prefixes = [d / view_tag for d in dirs]
            valid = [p for p in prefixes if _adjustments_ready(p, images, settings.image_names, cameras)]
            if len(valid) == 1:
                paths["ba_prefix"] = valid[0]

    if stage == "preliminary_dem" and not Path(paths["prelim_point_cloud"]).is_file():
        candidate = _unique_existing_candidate(
            asp_out.glob(f"dems/stereo_preliminary_*{model_suffix}/preliminary-PC.tif")
        )
        if candidate is not None:
            paths["prelim_point_cloud"] = candidate

    if stage == "lidar_alignment" and not Path(paths["prelim_dem"]).is_file():
        candidate = _unique_existing_candidate(
            asp_out.glob(f"dems/preliminary_*{model_suffix}-DEM.tif")
        )
        if candidate is not None:
            paths["prelim_dem"] = candidate
            match = re.search(r"preliminary_([ABC]{2})", candidate.name)
            if match:
                pair = match.group(1)
                paths["pair"] = pair
                paths["align_prefix"] = asp_out / "dems" / "align" / f"preliminary_{pair}_to_LiDAR{model_suffix}"
                paths["align_transform"] = Path(f"{paths['align_prefix']}-transform.txt")
                paths["align_log"] = paths["log_dir"] / f"pc_align.preliminary_{pair}_to_LiDAR{model_suffix}.log"

    if stage == "camera_transform" and not Path(paths["align_transform"]).is_file():
        candidate = _unique_existing_candidate(
            (asp_out / "dems" / "align").glob(f"*-transform.txt")
        )
        if candidate is not None:
            paths["align_transform"] = candidate

    if stage == "map_projection" and not _adjustments_ready(
        paths["aligned_ba_prefix"], images, settings.image_names, cameras
    ):
        dirs = sorted(asp_out.glob(f"ba_aligned{model_suffix}_{view_tag}"))
        prefixes = [d / view_tag for d in dirs]
        valid = [p for p in prefixes if _adjustments_ready(p, images, settings.image_names, cameras)]
        if len(valid) == 1:
            paths["aligned_ba_prefix"] = valid[0]

    return paths



def _select_restore_file(candidates, preferred_tokens=()):
    """Select the most plausible existing product for display-only restoration.

    Exact current-workflow paths are always tried before this helper.  This
    fallback exists for projects produced by older notebook releases whose
    filenames differ slightly from the current interface.  Preference tokens
    are used only for display restoration; no processing branch is rerun.
    """
    files = []
    seen = set()
    for candidate in candidates:
        path = Path(candidate)
        if not path.is_file():
            continue
        try:
            key = str(path.resolve())
        except Exception:
            key = str(path)
        if key in seen:
            continue
        seen.add(key)
        files.append(path)

    if not files:
        return None

    tokens = [str(token).lower() for token in preferred_tokens if str(token).strip()]

    def score(path):
        name = str(path).lower()
        token_score = sum(1 for token in tokens if token in name)
        # Prefer the canonical workflow mapprojection products over sensitivity
        # experiment filenames such as Al1m_MP50m when both exist.
        canonical_bonus = 1 if "_ba" in path.name.lower() else 0
        try:
            mtime = path.stat().st_mtime
        except Exception:
            mtime = 0.0
        return (token_score, canonical_bonus, mtime, str(path))

    return max(files, key=score)


def _restore_saved_preprocess_paths(saved_state, paths, views):
    """Reuse exact preprocessing product paths recorded in processing_state.json.

    Older interface releases already saved the resolved paths of important ASP
    products.  When reopening a project, those exact paths are more reliable than
    reconstructing filenames from current naming conventions.  Only paths that
    still exist are adopted; missing/stale entries are left for the legacy
    discovery fallback.
    """
    saved_state = saved_state or {}

    def existing_file(value):
        if not value:
            return None
        try:
            candidate = Path(value).expanduser()
        except Exception:
            return None
        return candidate if candidate.is_file() else None

    def existing_prefix(value):
        if not value:
            return None
        try:
            candidate = Path(value).expanduser()
        except Exception:
            return None
        # ASP adjustment prefixes are not files themselves.  Accept the saved
        # prefix when any sibling product begins with its basename.
        parent = candidate.parent
        if parent.is_dir() and any(parent.glob(candidate.name + "*")):
            return candidate
        return None

    saved_pc = existing_file(saved_state.get("preliminary_point_cloud"))
    if saved_pc is not None:
        paths["prelim_point_cloud"] = saved_pc
        if saved_pc.name.endswith("-PC.tif"):
            paths["prelim_prefix"] = saved_pc.with_name(saved_pc.name[:-7])
        paths["prelim_stereo_dir"] = saved_pc.parent

    saved_dem = existing_file(saved_state.get("preliminary_dem"))
    if saved_dem is not None:
        paths["prelim_dem"] = saved_dem
        if saved_dem.name.endswith("-DEM.tif"):
            paths["prelim_dem_prefix"] = saved_dem.with_name(saved_dem.name[:-8])

    saved_transform = existing_file(saved_state.get("alignment_transform"))
    if saved_transform is not None:
        paths["align_transform"] = saved_transform
        if saved_transform.name.endswith("-transform.txt"):
            paths["align_prefix"] = saved_transform.with_name(
                saved_transform.name[:-14]
            )

    saved_ba = existing_prefix(saved_state.get("ba_prefix"))
    if saved_ba is not None:
        paths["ba_prefix"] = saved_ba

    saved_aligned_ba = existing_prefix(saved_state.get("aligned_ba_prefix"))
    if saved_aligned_ba is not None:
        paths["aligned_ba_prefix"] = saved_aligned_ba

    saved_maps = saved_state.get("mapprojected_images")
    if isinstance(saved_maps, dict):
        for view in views:
            candidate = existing_file(saved_maps.get(view))
            if candidate is not None:
                paths["mapprojected"][view] = candidate

    return paths


def _restore_legacy_preprocess_paths(settings, processing, paths):
    """Resolve existing preprocessing products from both current and legacy names.

    This function is deliberately read-only.  It is used only when reopening an
    existing project so the UI can rebuild the same result tables/figures that
    were shown when the stages originally ran.
    """
    asp_out = Path(paths["asp_out"])
    log_dir = Path(paths["log_dir"])
    pair = str(paths.get("pair", processing.preliminary_pair)).upper()
    model_suffix = str(paths.get("model_suffix", ""))

    # Preliminary point cloud.
    if not Path(paths["prelim_point_cloud"]).is_file():
        candidate = _select_restore_file(
            list(asp_out.glob(f"dems/stereo_preliminary_{pair}*/preliminary-PC.tif"))
            + list(asp_out.glob("dems/stereo_preliminary_*/preliminary-PC.tif")),
            preferred_tokens=(pair, model_suffix),
        )
        if candidate is not None:
            paths["prelim_point_cloud"] = candidate
            paths["prelim_prefix"] = candidate.with_name("preliminary")
            paths["prelim_stereo_dir"] = candidate.parent
            import re as _re
            match = _re.search(r"stereo_preliminary_([ABC]{2})", candidate.parent.name)
            if match:
                pair = match.group(1)
                paths["pair"] = pair

    # Preliminary DSM.  First check the historical canonical names, then use
    # a recursive case-insensitive fallback because several early notebooks
    # stored the point2dem output in slightly different subfolders/names.
    if not Path(paths["prelim_dem"]).is_file():
        recursive_prelim_dems = [
            path
            for path in asp_out.rglob("*.tif")
            if "prelim" in path.name.lower()
            and "dem" in path.name.lower()
        ]

        # Some early projects switched between cropped_data and full_data while
        # keeping the same project folder.  If the active ASP root does not
        # contain the preliminary DSM, search the project itself rather than
        # requiring a rerun of point2dem.
        project_prelim_dems = []
        project_dir = getattr(settings, "project_dir", None)
        if project_dir is not None:
            project_dir = Path(project_dir)
            if project_dir.is_dir():
                project_prelim_dems = [
                    path
                    for path in project_dir.rglob("*.tif")
                    if "prelim" in path.name.lower()
                    and "dem" in path.name.lower()
                    and "mapproject" not in str(path).lower()
                ]

        candidate = _select_restore_file(
            list(asp_out.glob(f"dems/preliminary_{pair}*-DEM.tif"))
            + list(asp_out.glob("dems/preliminary_*-DEM.tif"))
            + recursive_prelim_dems
            + project_prelim_dems,
            preferred_tokens=(pair, model_suffix, "prelim", "asp_out"),
        )
        if candidate is not None:
            paths["prelim_dem"] = candidate
            name = candidate.name
            if name.lower().endswith("-dem.tif"):
                paths["prelim_dem_prefix"] = candidate.with_name(name[:-8])
            import re as _re
            match = _re.search(r"preliminary_([ABC]{2})", candidate.name, _re.I)
            if match:
                pair = match.group(1).upper()
                paths["pair"] = pair

    # Legacy preliminary DSM log names used `preliminary_point2dem.<pair>.log`.
    if not Path(paths["prelim_dem_log"]).is_file():
        candidate = _select_restore_file(
            list(log_dir.glob(f"*preliminary*point2dem*{pair}*.log"))
            + list(log_dir.glob(f"*point2dem*{pair}*.log")),
            preferred_tokens=(pair,),
        )
        if candidate is not None:
            paths["prelim_dem_log"] = candidate

    # pc_align transform and log.
    if not Path(paths["align_transform"]).is_file():
        candidate = _select_restore_file(
            list((asp_out / "dems" / "align").glob(f"*{pair}*-transform.txt"))
            + list((asp_out / "dems" / "align").glob("*-transform.txt")),
            preferred_tokens=(pair, "to_lidar", model_suffix),
        )
        if candidate is not None:
            paths["align_transform"] = candidate
            if candidate.name.endswith("-transform.txt"):
                paths["align_prefix"] = candidate.with_name(candidate.name[:-14])

    if not Path(paths["align_log"]).is_file():
        candidate = _select_restore_file(
            list(log_dir.glob(f"*pc_align*{pair}*.log"))
            + list(log_dir.glob("*pc_align*.log")),
            preferred_tokens=(pair,),
        )
        if candidate is not None:
            paths["align_log"] = candidate

    # Legacy/current mapprojection outputs.  Prefer the canonical baLxx product
    # for the configured map DEM over sensitivity-test copies.
    map_dir = Path(paths["mapproject_dir"])
    try:
        map_label = _dem_resolution_label(Path(processing.mapproject_dem))
    except Exception:
        map_label = ""
    raw_tag = f"{float(processing.raw_resolution_m):g}m"
    resolved_map = dict(paths["mapprojected"])
    for view in settings.image_names:
        expected = Path(resolved_map[view])
        if expected.is_file():
            continue
        candidates = (
            list(map_dir.glob(f"{view}_{settings.project_name}_{raw_tag}_ba*.tif"))
            + list(map_dir.glob(f"{view}_{settings.project_name}_*.tif"))
            + list(map_dir.glob(f"{view}_*.tif"))
        )
        candidate = _select_restore_file(
            candidates,
            preferred_tokens=(settings.project_name, raw_tag, f"ba{map_label}" if map_label else ""),
        )
        if candidate is not None:
            resolved_map[view] = candidate
    paths["mapprojected"] = resolved_map

    # Legacy/current per-view mapproject logs.
    resolved_logs = dict(paths["mapproject_logs"])
    for view in settings.image_names:
        if Path(resolved_logs[view]).is_file():
            continue
        candidate = _select_restore_file(
            list(log_dir.glob(f"mapproject.{view}*.log")),
            preferred_tokens=(view,),
        )
        if candidate is not None:
            resolved_logs[view] = candidate
    paths["mapproject_logs"] = resolved_logs

    return paths

def _resolve_single_stage_resume_context(settings, processing, selected_stages):
    """Resolve a later single stage against already completed products on disk."""
    paths = _preprocessing_paths(settings, processing)
    if len(selected_stages) != 1:
        return processing, paths, "current"

    stage = selected_stages[0]
    if _stage_prerequisites_ready(stage, settings, processing, paths):
        return processing, paths, "current"

    saved_processing = _processing_variant_from_saved_state(settings, processing)
    if saved_processing is not None:
        saved_paths = _preprocessing_paths(settings, saved_processing)
        if _stage_prerequisites_ready(stage, settings, saved_processing, saved_paths):
            return saved_processing, saved_paths, "saved project state"

    paths = _discover_stage_prerequisites_from_disk(stage, settings, processing, paths)
    if _stage_prerequisites_ready(stage, settings, processing, paths):
        return processing, paths, "unique existing product discovered on disk"

    return processing, paths, "current"


def run_pre_processing(
    settings: ProjectSettings,
    processing: PreProcessingSettings,
    progress_callback=None,
    result_callback=None,
    stages=None,
):
    """
    Run all preprocessing stages (default) or only selected stages.

    Stage-only execution reuses prerequisite files already present on disk,
    allowing a failed or experimentally modified stage to be rerun without
    repeating successful upstream ASP commands.
    """
    create_project_folders(settings)

    if settings.acquisition_mode == "tri_stereo" and not _tri_stereo_normalization_ready(settings):
        raise ValueError(
            "Run Metadata and geometry after Prepare data before ASP preprocessing. "
            "The tri-stereo prepared copies must first be normalized to "
            "A=Forward (F), B=Middle/near-nadir (M), C=Backward (B)."
        )

    selected_stages = _normalized_preprocess_stages(stages)
    selected_set = set(selected_stages)
    run_all = selected_stages == PREPROCESS_STAGE_ORDER

    # For a single-stage resume, first try the current UI configuration, then
    # automatically reuse the last successful project configuration / unique
    # upstream product already on disk. This removes the artificial need to
    # rerun the immediately preceding stage after reopening the notebook.
    processing, paths, resume_context = _resolve_single_stage_resume_context(
        settings, processing, selected_stages
    )

    ba_cost_function = _clean_optional_string(processing.ba_cost_function)
    # The interface suggests ASP's documented values but deliberately allows
    # a custom string for newer/experimental ASP builds. If blank, the option
    # is omitted and ASP falls back to its documented default. ASP itself
    # remains the authority and will reject an unsupported value with a logged error.

    alignment_dem = Path(
        processing.alignment_dem
    ).expanduser() if str(processing.alignment_dem).strip() else None

    mapproject_dem = Path(
        processing.mapproject_dem
    ).expanduser() if str(processing.mapproject_dem).strip() else None

    if "lidar_alignment" in selected_set:
        if alignment_dem is None:
            raise ValueError(
                "Reference alignment requires an alignment reference DEM."
            )
        _require_existing_files(alignment_dem)

    if "map_projection" in selected_set:
        if mapproject_dem is None:
            raise ValueError(
                "Map projection requires a map-projection reference DEM."
            )
        _require_existing_files(mapproject_dem)

    suffix = "" if run_all else "_" + "_".join(selected_stages)
    summary_log = settings.log_dir / f"pre_processing{suffix}.log"

    current_stage = None
    current_log = None

    residual_summary = None
    residual_csv = None
    prelim_plot = None
    alignment_results = None
    camera_table = None
    map_table = None
    map_plot = None

    def emit(stage, status, payload=None):
        if result_callback is not None:
            result_callback(
                stage,
                status,
                payload or {},
            )

    with WorkflowLog(summary_log) as summary:
        try:
            images = paths["images"]
            cameras = paths["cameras"]
            session_type = paths["session_type"]

            summary.write(
                "Selected preprocessing stages: "
                + ", ".join(selected_stages)
            )
            if not run_all:
                summary.write(f"Single-stage prerequisite context: {resume_context}")

            # ------------------------------------------------
            # 1. BUNDLE ADJUSTMENT
            # ------------------------------------------------
            if "bundle_adjustment" in selected_set:
                current_stage = "bundle_adjustment"
                current_log = paths["ba_log"]

                if progress_callback:
                    progress_callback(5, "Bundle adjustment")

                emit(
                    current_stage,
                    "running",
                    {
                        "camera_model": paths["camera_model"],
                        "session_type": session_type,
                        "robust_threshold": processing.ba_robust_threshold,
                        "max_iterations": processing.ba_max_iterations,
                        "cost_function": ba_cost_function,
                        "datum": "WGS84",
                        "log": paths["ba_log"],
                    },
                )

                _prepare_output_prefix(
                    paths["ba_prefix"],
                    paths["ba_log"],
                    settings.overwrite,
                )

                # Exact Airbus DIM cameras are backed by the USGS CSM linescan
                # model. Validate image/camera pairing before the more expensive BA.
                # The DIM XML deliberately keeps its DIM_A/DIM_B/DIM_C name; ASP
                # pairs images and cameras by command-line order, not basename.
                if paths["camera_model"] == "pleiades":
                    _preflight_exact_pleiades_cameras(settings, paths)

                ba_args = [
                    "-t", session_type,
                    *[images[view] for view in settings.image_names],
                    *[cameras[view] for view in settings.image_names],
                ]

                # Follow ASP's documented Pléiades exact-camera recipe. This is
                # especially important for ASP 3.3.x, where tri-weight defaulted
                # to 0 rather than the later 0.1 default.
                if paths["camera_model"] == "pleiades":
                    ba_args.extend([
                        "--camera-weight", 0,
                        "--tri-weight", 0.1,
                    ])

                if ba_cost_function is not None:
                    ba_args.extend(["--cost-function", ba_cost_function])
                if processing.ba_robust_threshold is not None:
                    ba_args.extend(["--robust-threshold", processing.ba_robust_threshold])
                if processing.ba_max_iterations is not None:
                    ba_args.extend(["--num-iterations", processing.ba_max_iterations])

                ba_args.extend([
                    "--datum", "WGS84",
                    "--threads", 0,
                    "--tif-compress", "Deflate",
                    "-o", paths["ba_prefix"],
                ])

                _run_asp_command(
                    "bundle_adjust",
                    ba_args,
                    paths["ba_log"],
                    paths["processing_root"],
                )

                ba_adjustments = _require_adjustments(
                    paths["ba_prefix"], images, settings.image_names, cameras
                )

                adjustment_table = pd.DataFrame([
                    {
                        "View": view,
                        "Image": Path(images[view]).name,
                        "Camera XML": Path(cameras[view]).name,
                        "ASP session": f"-t {session_type}",
                        "Adjustment / state": Path(adjustment).name,
                    }
                    for view, adjustment in zip(settings.image_names, ba_adjustments)
                ])

                residual_summary = _bundle_adjustment_residual_summary(
                    paths["ba_prefix"], settings.image_names
                )
                residual_csv = (
                    settings.metadata_dir / "bundle_adjustment_residuals.csv"
                )
                residual_summary.to_csv(residual_csv)

                emit(
                    current_stage,
                    "completed",
                    {
                        "table": residual_summary,
                        "adjustment_table": adjustment_table,
                        "csv": residual_csv,
                        "prefix": paths["ba_prefix"],
                        "camera_model": paths["camera_model"],
                        "session_type": session_type,
                        "robust_threshold": processing.ba_robust_threshold,
                        "max_iterations": processing.ba_max_iterations,
                        "cost_function": ba_cost_function,
                        "datum": "WGS84",
                        "log": paths["ba_log"],
                    },
                )

                summary.write(
                    f"Bundle adjustment completed: {paths['ba_prefix']}"
                )

            # ------------------------------------------------
            # 2. INITIAL STEREO CORRELATION
            # ------------------------------------------------
            if "preliminary_stereo" in selected_set:
                current_stage = "preliminary_stereo"
                current_log = paths["prelim_log"]

                _require_adjustments(
                    paths["ba_prefix"], images, settings.image_names, cameras
                )

                if progress_callback:
                    progress_callback(
                        25, f"Preliminary stereo {paths['pair']}"
                    )

                left = paths["pair"][0]
                right = paths["pair"][1]

                stereo_payload = {
                    "pair": paths["pair"],
                    "left": images[left],
                    "right": images[right],
                    "prefix": paths["prelim_prefix"],
                    "algorithm": processing.prelim_stereo_algorithm,
                    "cost_mode": processing.prelim_cost_mode,
                    "corr_kernel": processing.prelim_corr_kernel,
                    "subpixel_kernel": processing.prelim_subpixel_kernel,
                    "xcorr_threshold": processing.prelim_xcorr_threshold,
                    "subpixel_mode": processing.prelim_subpixel_mode,
                    "corr_memory_limit_mb": processing.corr_memory_limit_mb,
                    "corr_tile_size": processing.corr_tile_size,
                    "log": paths["prelim_log"],
                }
                emit(current_stage, "running", stereo_payload)

                _prepare_run_directory(
                    paths["prelim_stereo_dir"],
                    paths["prelim_log"],
                    settings.overwrite,
                )

                _run_asp_command(
                    "parallel_stereo",
                    [
                        "-t", session_type,
                        "--stereo-algorithm", processing.prelim_stereo_algorithm,
                        "--xcorr-threshold", processing.prelim_xcorr_threshold,
                        "--cost-mode", processing.prelim_cost_mode,
                        "--corr-kernel",
                        processing.prelim_corr_kernel,
                        processing.prelim_corr_kernel,
                        "--subpixel-kernel",
                        processing.prelim_subpixel_kernel,
                        processing.prelim_subpixel_kernel,
                        "--corr-tile-size", processing.corr_tile_size,
                        "--corr-memory-limit-mb", processing.corr_memory_limit_mb,
                        "--subpixel-mode", processing.prelim_subpixel_mode,
                        "--bundle-adjust-prefix", paths["ba_prefix"],
                        images[left], images[right],
                        cameras[left], cameras[right],
                        paths["prelim_prefix"],
                    ],
                    paths["prelim_log"],
                    paths["processing_root"],
                )

                _require_existing_files(paths["prelim_point_cloud"])
                point_cloud_info = _raster_basic_summary(
                    paths["prelim_point_cloud"]
                )

                emit(
                    current_stage,
                    "completed",
                    {
                        **stereo_payload,
                        "point_cloud": paths["prelim_point_cloud"],
                        "point_cloud_info": point_cloud_info,
                    },
                )
                summary.write(
                    f"Preliminary point cloud: {paths['prelim_point_cloud']}"
                )

            # ------------------------------------------------
            # 3. PRELIMINARY ALIGNMENT DEM
            # ------------------------------------------------
            if "preliminary_dem" in selected_set:
                current_stage = "preliminary_dem"
                current_log = paths["prelim_dem_log"]

                _require_existing_files(paths["prelim_point_cloud"])

                if progress_callback:
                    progress_callback(45, "Preliminary alignment DEM")

                dem_payload = {
                    "point_cloud": paths["prelim_point_cloud"],
                    "dem": paths["prelim_dem"],
                    "resolution_m": processing.prelim_dem_resolution_m,
                    "nodata": processing.prelim_dem_nodata,
                    "target_epsg": processing.target_epsg,
                    "log": paths["prelim_dem_log"],
                }
                emit(current_stage, "running", dem_payload)

                _prepare_output_prefix(
                    paths["prelim_dem_prefix"],
                    paths["prelim_dem_log"],
                    settings.overwrite,
                )

                _run_asp_command(
                    "point2dem",
                    [
                        "--t_srs", f"EPSG:{processing.target_epsg}",
                        "--tr", processing.prelim_dem_resolution_m,
                        "--threads", 0,
                        "--nodata-value", processing.prelim_dem_nodata,
                        paths["prelim_point_cloud"],
                        "-o", paths["prelim_dem_prefix"],
                    ],
                    paths["prelim_dem_log"],
                    paths["processing_root"],
                )

                _require_existing_files(paths["prelim_dem"])
                prelim_plot = _plot_preliminary_dem_from_path(
                    settings, paths["prelim_dem"], paths["pair"]
                )
                dem_info = _raster_basic_summary(paths["prelim_dem"])

                emit(
                    current_stage,
                    "completed",
                    {
                        **dem_payload,
                        "dem_info": dem_info,
                        "plot": prelim_plot,
                    },
                )
                summary.write(f"Preliminary DEM: {paths['prelim_dem']}")

            # ------------------------------------------------
            # 4. pc_align TO EXTERNAL REFERENCE DEM
            # ------------------------------------------------
            if "lidar_alignment" in selected_set:
                current_stage = "lidar_alignment"
                current_log = paths["align_log"]

                _require_existing_files(paths["prelim_dem"], alignment_dem)

                if progress_callback:
                    progress_callback(
                        60, "Aligning preliminary DEM to reference DEM"
                    )

                align_payload = {
                    "reference": alignment_dem,
                    "source": paths["prelim_dem"],
                    "transform": paths["align_transform"],
                    "max_displacement_m": processing.pc_align_max_displacement_m,
                    "iterations": processing.pc_align_iterations,
                    "log": paths["align_log"],
                }
                emit(current_stage, "running", align_payload)

                _prepare_output_prefix(
                    paths["align_prefix"],
                    paths["align_log"],
                    settings.overwrite,
                )

                _run_asp_command(
                    "pc_align",
                    [
                        "--max-displacement",
                        processing.pc_align_max_displacement_m,
                        "--num-iterations",
                        processing.pc_align_iterations,
                        alignment_dem,
                        paths["prelim_dem"],
                        "-o", paths["align_prefix"],
                    ],
                    paths["align_log"],
                    paths["processing_root"],
                )

                _require_existing_files(paths["align_transform"])
                alignment_results = _parse_pc_align_original_results(
                    paths["align_log"], paths["align_transform"]
                )

                emit(
                    current_stage,
                    "completed",
                    {
                        **align_payload,
                        "alignment_results": alignment_results,
                    },
                )
                summary.write(
                    f"Alignment transform: {paths['align_transform']}"
                )

            # ------------------------------------------------
            # 5. APPLY TRANSFORM TO ALL CAMERA MODELS
            # ------------------------------------------------
            if "camera_transform" in selected_set:
                current_stage = "camera_transform"
                current_log = paths["aligned_ba_log"]

                _require_existing_files(paths["align_transform"])
                _require_adjustments(
                    paths["ba_prefix"], images, settings.image_names, cameras
                )

                if progress_callback:
                    progress_callback(
                        72, "Applying alignment transform to cameras"
                    )

                camera_payload = {
                    "transform": paths["align_transform"],
                    "prefix": paths["aligned_ba_prefix"],
                    "threads": processing.aligned_ba_threads,
                    "session_type": session_type,
                    "log": paths["aligned_ba_log"],
                }
                emit(current_stage, "running", camera_payload)

                _prepare_output_prefix(
                    paths["aligned_ba_prefix"],
                    paths["aligned_ba_log"],
                    settings.overwrite,
                )

                _run_asp_command(
                    "bundle_adjust",
                    [
                        "-t", session_type,
                        *[images[view] for view in settings.image_names],
                        *[cameras[view] for view in settings.image_names],
                        "--initial-transform", paths["align_transform"],
                        "--input-adjustments-prefix", paths["ba_prefix"],
                        "--apply-initial-transform-only",
                        "--datum", "WGS84",
                        "--threads", processing.aligned_ba_threads,
                        "--tif-compress", "Deflate",
                        "-o", paths["aligned_ba_prefix"],
                    ],
                    paths["aligned_ba_log"],
                    paths["processing_root"],
                )

                _require_adjustments(
                    paths["aligned_ba_prefix"], images, settings.image_names, cameras
                )
                camera_table = _camera_adjustment_table(settings, paths)

                emit(
                    current_stage,
                    "completed",
                    {**camera_payload, "table": camera_table},
                )

            # ------------------------------------------------
            # 6. MAP PROJECT ALL ACTIVE IMAGES
            # ------------------------------------------------
            if "map_projection" in selected_set:
                current_stage = "map_projection"
                current_log = None

                _require_existing_files(mapproject_dem)
                _require_adjustments(
                    paths["aligned_ba_prefix"], images, settings.image_names, cameras
                )

                if progress_callback:
                    progress_callback(82, "Map-projecting active images")

                map_payload = {
                    "mapproject_dem": mapproject_dem,
                    "outputs": paths["mapprojected"],
                    "logs": paths["mapproject_logs"],
                    "resolution_m": processing.raw_resolution_m,
                    "target_epsg": processing.target_epsg,
                    "threads": processing.mapproject_threads,
                }
                emit(current_stage, "running", map_payload)

                paths["mapproject_dir"].mkdir(parents=True, exist_ok=True)

                for index, view in enumerate(settings.image_names, start=1):
                    output_image = paths["mapprojected"][view]
                    current_log = paths["mapproject_logs"][view]

                    _prepare_output_prefix(
                        output_image,
                        current_log,
                        settings.overwrite,
                    )

                    _run_asp_command(
                        "mapproject",
                        [
                            "-t", session_type,
                            "--threads", processing.mapproject_threads,
                            "--tr", processing.raw_resolution_m,
                            "--t_srs", f"EPSG:{processing.target_epsg}",
                            "--bundle-adjust-prefix",
                            paths["aligned_ba_prefix"],
                            "--tif-compress", "Deflate",
                            mapproject_dem,
                            images[view],
                            cameras[view],
                            output_image,
                        ],
                        current_log,
                        paths["processing_root"],
                    )
                    _require_existing_files(output_image)

                    if progress_callback:
                        progress_callback(
                            82 + int(index / len(settings.image_names) * 14),
                            f"Map-projected {view}",
                        )

                map_plot = _plot_mapprojected_from_paths(
                    settings,
                    paths["mapprojected"],
                    processing.target_epsg,
                )
                map_table = _mapproject_output_table(settings, paths)

                emit(
                    current_stage,
                    "completed",
                    {
                        **map_payload,
                        "table": map_table,
                        "plot": map_plot,
                    },
                )

            state_path = _save_processing_state(
                settings,
                "pre_processing",
                {
                    "last_run_stages": list(selected_stages),
                    "alignment_dem": (
                        str(alignment_dem) if alignment_dem is not None else ""
                    ),
                    "mapproject_dem": (
                        str(mapproject_dem) if mapproject_dem is not None else ""
                    ),
                    "target_epsg": processing.target_epsg,
                    "camera_model": paths["camera_model"],
                    "session_type": paths["session_type"],
                    "camera_files": {
                        view: str(paths["cameras"][view])
                        for view in settings.image_names
                    },
                    "raw_resolution_m": processing.raw_resolution_m,
                    "preliminary_pair": paths["pair"],
                    "preliminary_stereo_algorithm": (
                        processing.prelim_stereo_algorithm
                    ),
                    "preliminary_cost_mode": processing.prelim_cost_mode,
                    "preliminary_corr_kernel": processing.prelim_corr_kernel,
                    "preliminary_subpixel_kernel": (
                        processing.prelim_subpixel_kernel
                    ),
                    "preliminary_xcorr_threshold": (
                        processing.prelim_xcorr_threshold
                    ),
                    "preliminary_subpixel_mode": (
                        processing.prelim_subpixel_mode
                    ),
                    "ba_prefix": str(paths["ba_prefix"]),
                    "preliminary_point_cloud": str(paths["prelim_point_cloud"]),
                    "preliminary_dem": str(paths["prelim_dem"]),
                    "alignment_transform": str(paths["align_transform"]),
                    "aligned_ba_prefix": str(paths["aligned_ba_prefix"]),
                    "mapprojected_images": {
                        view: str(paths["mapprojected"][view])
                        for view in settings.image_names
                    },
                },
            )

            if progress_callback:
                label = (
                    "Pre-processing completed"
                    if run_all
                    else "Selected pre-processing step completed"
                )
                progress_callback(100, label)

            return {
                "selected_stages": selected_stages,
                "paths": paths,
                "residual_summary": residual_summary,
                "residual_csv": residual_csv,
                "preliminary_plot": prelim_plot,
                "alignment_results": alignment_results,
                "camera_table": camera_table,
                "map_table": map_table,
                "map_plot": map_plot,
                "summary_log": summary_log,
                "state_path": state_path,
            }

        except WorkflowCancelled as exc:
            summary.write("")
            summary.write("STOPPED BY USER")
            summary.write(str(exc))

            if current_stage is not None:
                emit(
                    current_stage,
                    "stopped",
                    {
                        "error": str(exc),
                        "log": current_log,
                    },
                )

            raise

        except Exception as exc:
            summary.write("")
            summary.write("ERROR")
            summary.write(traceback.format_exc())

            if current_stage is not None:
                emit(
                    current_stage,
                    "failed",
                    {
                        "error": f"{type(exc).__name__}: {exc}",
                        "log": current_log,
                    },
                )

            raise



# ============================================================
# FINAL POINT-CLOUD PATHS AND SELECTION
# ============================================================

def _normalize_internal_tag(tag: str) -> str:
    return (
        str(tag)
        .replace(" ", "")
        .replace(",", "")
        .upper()
    )


def _validate_final_selection(
    settings: ProjectSettings,
    final: FinalProcessingSettings,
):
    # Resolve the algorithm here so invalid empty custom values fail early.
    _resolve_final_algorithm(final)

    if not final.kernel_pairs:
        raise ValueError(
            "Select or enter at least one linked CK:SK kernel pair."
        )

    for pair in final.kernel_pairs:
        if (
            len(pair) != 2
            or int(pair[0]) <= 0
            or int(pair[1]) <= 0
        ):
            raise ValueError(
                f"Invalid CK:SK pair: {pair}. "
                "Both values must be positive integers."
            )

    if final.stereo_mode == "single":
        pairs = tuple(
            _normalize_internal_tag(tag)
            for tag in final.single_pairs
        )

        if not pairs:
            raise ValueError(
                "Select at least one stereo pair."
            )

        for pair in pairs:
            if (
                len(pair) != 2
                or pair[0] == pair[1]
                or any(
                    view not in settings.image_names
                    for view in pair
                )
            ):
                raise ValueError(
                    f"Invalid stereo pair: {pair}"
                )

        return {
            "single_pairs": pairs,
            "dual_configurations": (),
            "tri_merge_required": False,
        }

    if final.stereo_mode == "dual":
        if settings.acquisition_mode != "tri_stereo":
            raise ValueError(
                "Ordered three-image configurations "
                "require tri-stereo A/B/C."
            )

        configurations = tuple(
            _normalize_internal_tag(tag)
            for tag in final.dual_configurations
        )

        if not configurations:
            raise ValueError(
                "Select at least one ordered "
                "three-image configuration."
            )

        for config in configurations:
            if (
                len(config) != 3
                or set(config) != {"A", "B", "C"}
            ):
                raise ValueError(
                    f"Invalid ordered configuration: "
                    f"{config}"
                )

        return {
            "single_pairs": (),
            "dual_configurations": configurations,
            "tri_merge_required": False,
        }

    if final.stereo_mode == "tri":
        if settings.acquisition_mode != "tri_stereo":
            raise ValueError(
                "Tri merge requires A/B/C."
            )

        return {
            "single_pairs": ("AB", "AC", "BC"),
            "dual_configurations": (),
            "tri_merge_required": True,
        }

    raise ValueError(
        f"Unsupported stereo mode: "
        f"{final.stereo_mode}"
    )


def _final_common_paths(
    settings: ProjectSettings,
    processing: PreProcessingSettings,
):
    pre_paths = _preprocessing_paths(
        settings,
        processing,
    )

    _require_existing_files(
        Path(processing.mapproject_dem),
        *[
            pre_paths["mapprojected"][view]
            for view in settings.image_names
        ],
    )

    # Do not hard-code RPC-style A.adjust/B.adjust/C.adjust here.  Exact
    # Pléiades DIM runs are named from the camera files and normally produce
    # DIM_A/DIM_B/DIM_C adjustment (or adjusted-state) products.
    _require_adjustments(
        pre_paths["aligned_ba_prefix"],
        pre_paths["images"],
        settings.image_names,
        pre_paths["cameras"],
    )

    return pre_paths


def _configuration_views(tag: str):
    return list(tag)


def _point_cloud_output(
    settings: ProjectSettings,
    processing: PreProcessingSettings,
    final: FinalProcessingSettings,
    config_tag: str,
    ck: int,
    sk: int,
):
    asp_out = _processing_output_dir(settings)

    algorithm_file_tag = _safe_algorithm_filename(
        _resolve_final_algorithm(final)["display_name"]
    )

    outname = (
        f"{settings.project_name}_"
        f"{algorithm_file_tag}_"
        f"ck{ck}_sk{sk}"
        f"{_camera_model_suffix(processing)}"
    )

    run_directory = (
        asp_out
        / "point_clouds"
        / f"stereo_{config_tag}_{outname}"
    )

    run_prefix = (
        run_directory
        / f"{config_tag}_{outname}"
    )

    point_cloud = Path(
        f"{run_prefix}-PC.tif"
    )

    log_file = (
        _processing_log_dir(settings)
        / f"stereo.{config_tag}_{outname}.log"
    )

    return {
        "outname": outname,
        "run_directory": run_directory,
        "run_prefix": run_prefix,
        "point_cloud": point_cloud,
        "log_file": log_file,
    }


def _run_one_final_configuration(
    settings: ProjectSettings,
    processing: PreProcessingSettings,
    final: FinalProcessingSettings,
    pre_paths: dict,
    config_tag: str,
    ck: int,
    sk: int,
):
    config_tag = _normalize_internal_tag(
        config_tag
    )

    views = _configuration_views(
        config_tag
    )

    if len(views) not in (2, 3):
        raise ValueError(
            f"Invalid stereo configuration: "
            f"{config_tag}"
        )

    map_images = [
        pre_paths["mapprojected"][view]
        for view in views
    ]

    cameras = [
        pre_paths["cameras"][view]
        for view in views
    ]

    _require_existing_files(
        Path(processing.mapproject_dem),
        *map_images,
        *cameras,
    )

    product_paths = _point_cloud_output(
        settings,
        processing,
        final,
        config_tag,
        ck,
        sk,
    )

    _prepare_run_directory(
        product_paths["run_directory"],
        product_paths["log_file"],
        settings.overwrite,
    )

    algorithm = _resolve_final_algorithm(final)

    _run_asp_command(
        "parallel_stereo",
        [
            "-t",
            pre_paths["session_type"],
            "--alignment-method",
            "none",
            "--stereo-algorithm",
            algorithm["asp_algorithm"],
            "--xcorr-threshold",
            final.xcorr_threshold,
            "--cost-mode",
            algorithm["cost_mode"],
            "--corr-kernel",
            ck,
            ck,
            "--subpixel-kernel",
            sk,
            sk,
            "--corr-tile-size",
            final.corr_tile_size,
            "--corr-memory-limit-mb",
            final.corr_memory_limit_mb,
            "--subpixel-mode",
            final.subpixel_mode,
            "--bundle-adjust-prefix",
            pre_paths["aligned_ba_prefix"],
            *map_images,
            *cameras,
            product_paths["run_prefix"],
            Path(processing.mapproject_dem),
        ],
        product_paths["log_file"],
        _processing_root(settings),
    )

    _require_existing_files(
        product_paths["point_cloud"]
    )

    return {
        "product_tag": config_tag,
        "geometry": GEOMETRY_LABELS.get(
            config_tag,
            config_tag,
        ),
        "algorithm": _resolve_final_algorithm(final)["display_name"],
        "correlation_kernel": ck,
        "subpixel_kernel": sk,
        "point_cloud": (
            product_paths["point_cloud"]
        ),
        "log": product_paths["log_file"],
    }


def _merge_tri_point_clouds(
    settings: ProjectSettings,
    processing: PreProcessingSettings,
    final: FinalProcessingSettings,
    pair_products: Sequence[dict],
    ck: int,
    sk: int,
):
    selected = {
        item["product_tag"]: item["point_cloud"]
        for item in pair_products
        if (
            item["correlation_kernel"] == ck
            and item["subpixel_kernel"] == sk
        )
    }

    for tag in ("AB", "AC", "BC"):
        if tag not in selected:
            raise RuntimeError(
                f"Cannot merge tri-stereo point "
                f"clouds because {tag} is missing."
            )

    algorithm_file_tag = _safe_algorithm_filename(
        _resolve_final_algorithm(final)["display_name"]
    )

    outname = (
        f"{settings.project_name}_"
        f"{algorithm_file_tag}_"
        f"ck{ck}_sk{sk}"
        f"{_camera_model_suffix(processing)}"
    )

    merge_tag = "ABACBC"

    merged_directory = (
        _processing_output_dir(settings)
        / "point_clouds_merged"
        / f"merged_{merge_tag}_{outname}"
    )

    merged_point_cloud = (
        merged_directory
        / f"{merge_tag}_{outname}-PC.tif"
    )

    merge_log = (
        _processing_log_dir(settings)
        / f"pc_merge.{merge_tag}_{outname}.log"
    )

    _prepare_run_directory(
        merged_directory,
        merge_log,
        settings.overwrite,
    )

    _run_asp_command(
        "pc_merge",
        [
            selected["AB"],
            selected["AC"],
            selected["BC"],
            "-o",
            merged_point_cloud,
            "--threads",
            final.pc_merge_threads,
            "--tif-compress",
            "LZW",
        ],
        merge_log,
        _processing_root(settings),
    )

    _require_existing_files(
        merged_point_cloud
    )

    return {
        "product_tag": merge_tag,
        "geometry": GEOMETRY_LABELS[
            merge_tag
        ],
        "algorithm": _resolve_final_algorithm(final)["display_name"],
        "correlation_kernel": ck,
        "subpixel_kernel": sk,
        "point_cloud": merged_point_cloud,
        "log": merge_log,
    }


# ============================================================
# RUN FINAL POINT-CLOUD RECONSTRUCTION
# Exact logic from notebook 06:
# single / dual / tri and pc_merge for AB+AC+BC.
# ============================================================

def run_point_cloud_reconstruction(
    settings: ProjectSettings,
    processing: PreProcessingSettings,
    final: FinalProcessingSettings,
    progress_callback=None,
):
    selection = _validate_final_selection(
        settings,
        final,
    )

    pre_paths = _final_common_paths(
        settings,
        processing,
    )

    summary_log = (
        settings.log_dir
        / "point_cloud_reconstruction.log"
    )

    products = []

    with WorkflowLog(summary_log) as summary:
        try:
            configs = []

            for pair in selection["single_pairs"]:
                configs.append(pair)

            for config in selection[
                "dual_configurations"
            ]:
                configs.append(config)

            total = (
                len(configs)
                * len(final.kernel_pairs)
            )

            completed = 0

            for config_tag in configs:
                for ck, sk in final.kernel_pairs:
                    if progress_callback:
                        progress_callback(
                            5
                            + int(
                                completed
                                / max(total, 1)
                                * 75
                            ),
                            (
                                f"Stereo {config_tag} — "
                                f"{final.algorithm_tag} "
                                f"{ck}:{sk}"
                            ),
                        )

                    products.append(
                        _run_one_final_configuration(
                            settings,
                            processing,
                            final,
                            pre_paths,
                            config_tag,
                            ck,
                            sk,
                        )
                    )

                    completed += 1

            if selection[
                "tri_merge_required"
            ]:
                merged_products = []

                for index, (ck, sk) in enumerate(
                    final.kernel_pairs,
                    start=1,
                ):
                    if progress_callback:
                        progress_callback(
                            82
                            + int(
                                index
                                / len(
                                    final.kernel_pairs
                                )
                                * 14
                            ),
                            (
                                "Merging AB + AC + BC "
                                f"for {ck}:{sk}"
                            ),
                        )

                    merged_products.append(
                        _merge_tri_point_clouds(
                            settings,
                            processing,
                            final,
                            products,
                            ck,
                            sk,
                        )
                    )

                selected_products = (
                    merged_products
                )
            else:
                selected_products = products

            products_df = pd.DataFrame(
                [
                    {
                        "Product": item[
                            "product_tag"
                        ],
                        "Geometry": item[
                            "geometry"
                        ],
                        "Algorithm": item[
                            "algorithm"
                        ],
                        "CK": item[
                            "correlation_kernel"
                        ],
                        "SK": item[
                            "subpixel_kernel"
                        ],
                        "Point cloud": str(
                            item["point_cloud"]
                        ),
                    }
                    for item
                    in selected_products
                ]
            )

            csv_path = (
                settings.metadata_dir
                / "point_cloud_products.csv"
            )

            products_df.to_csv(
                csv_path,
                index=False,
            )

            state_path = _save_processing_state(
                settings,
                "point_cloud_reconstruction",
                {
                    "stereo_mode": (
                        final.stereo_mode
                    ),
                    "camera_model": pre_paths["camera_model"],
                    "session_type": pre_paths["session_type"],
                    "algorithm": _resolve_final_algorithm(final)[
                        "display_name"
                    ],
                    "kernel_pairs": [
                        list(pair)
                        for pair
                        in final.kernel_pairs
                    ],
                    "selected_products": [
                        {
                            "product_tag": item[
                                "product_tag"
                            ],
                            "point_cloud": str(
                                item[
                                    "point_cloud"
                                ]
                            ),
                            "ck": item[
                                "correlation_kernel"
                            ],
                            "sk": item[
                                "subpixel_kernel"
                            ],
                        }
                        for item
                        in selected_products
                    ],
                },
            )

            if progress_callback:
                progress_callback(
                    100,
                    "Point-cloud reconstruction completed",
                )

            return {
                "all_pair_products": products,
                "selected_products": selected_products,
                "products_df": products_df,
                "products_csv": csv_path,
                "summary_log": summary_log,
                "state_path": state_path,
            }

        except Exception:
            summary.write("")
            summary.write("ERROR")
            summary.write(
                traceback.format_exc()
            )
            raise


# ============================================================
# FINAL DSM GENERATION
# Kept separate from expensive stereo correlation, exactly as
# in the original workflow design.
# ============================================================

def _selected_point_clouds_for_dsm(
    settings: ProjectSettings,
    processing: PreProcessingSettings,
    final: FinalProcessingSettings,
):
    """
    Reconstruct the expected selected point-cloud paths from the
    current interface selection.  This allows point2dem to be rerun
    without rerunning parallel_stereo.
    """
    selection = _validate_final_selection(
        settings,
        final,
    )

    products = []

    for ck, sk in final.kernel_pairs:
        algorithm_file_tag = _safe_algorithm_filename(
            _resolve_final_algorithm(final)["display_name"]
        )

        outname = (
            f"{settings.project_name}_"
            f"{algorithm_file_tag}_"
            f"ck{ck}_sk{sk}"
            f"{_camera_model_suffix(processing)}"
        )

        if final.stereo_mode == "single":
            tags = selection["single_pairs"]

            for tag in tags:
                point_cloud = (
                    _processing_output_dir(settings)
                    / "point_clouds"
                    / f"stereo_{tag}_{outname}"
                    / f"{tag}_{outname}-PC.tif"
                )

                products.append(
                    {
                        "product_tag": tag,
                        "geometry": (
                            GEOMETRY_LABELS.get(
                                tag,
                                tag,
                            )
                        ),
                        "algorithm": _resolve_final_algorithm(final)[
                            "display_name"
                        ],
                        "correlation_kernel": ck,
                        "subpixel_kernel": sk,
                        "point_cloud": point_cloud,
                    }
                )

        elif final.stereo_mode == "dual":
            tags = selection[
                "dual_configurations"
            ]

            for tag in tags:
                point_cloud = (
                    _processing_output_dir(settings)
                    / "point_clouds"
                    / f"stereo_{tag}_{outname}"
                    / f"{tag}_{outname}-PC.tif"
                )

                products.append(
                    {
                        "product_tag": tag,
                        "geometry": (
                            GEOMETRY_LABELS.get(
                                tag,
                                tag,
                            )
                        ),
                        "algorithm": _resolve_final_algorithm(final)[
                            "display_name"
                        ],
                        "correlation_kernel": ck,
                        "subpixel_kernel": sk,
                        "point_cloud": point_cloud,
                    }
                )

        elif final.stereo_mode == "tri":
            tag = "ABACBC"

            point_cloud = (
                _processing_output_dir(settings)
                / "point_clouds_merged"
                / f"merged_{tag}_{outname}"
                / f"{tag}_{outname}-PC.tif"
            )

            products.append(
                {
                    "product_tag": tag,
                    "geometry": (
                        GEOMETRY_LABELS[tag]
                    ),
                    "algorithm": _resolve_final_algorithm(final)[
                        "display_name"
                    ],
                    "correlation_kernel": ck,
                    "subpixel_kernel": sk,
                    "point_cloud": point_cloud,
                }
            )

    _require_existing_files(
        *[
            item["point_cloud"]
            for item in products
        ]
    )

    return products


def _plot_one_final_dsm(
    settings: ProjectSettings,
    product: dict,
):
    dem, bounds, crs = _read_dem_preview(
        product["dem"]
    )

    valid = dem.compressed()

    if valid.size == 0:
        raise ValueError(
            f"Final DSM has no valid values:\n"
            f"{product['dem']}"
        )

    vmin, vmax = np.percentile(
        valid,
        [2, 98],
    )

    filled = dem.filled(
        float(np.median(valid))
    )

    hillshade = LightSource(
        azdeg=315,
        altdeg=45,
    ).hillshade(
        filled,
        vert_exag=1.0,
    )

    hillshade = np.ma.array(
        hillshade,
        mask=np.ma.getmaskarray(dem),
    )

    extent = [
        bounds.left,
        bounds.right,
        bounds.bottom,
        bounds.top,
    ]

    has_error = (
        product.get("error_image")
        is not None
        and Path(
            product["error_image"]
        ).is_file()
    )

    ncols = 2 if has_error else 1

    fig, axes = plt.subplots(
        1,
        ncols,
        figsize=(7.4 * ncols, 6.1),
        squeeze=False,
    )

    ax = axes[0, 0]

    dem_plot = ax.imshow(
        dem,
        extent=extent,
        origin="upper",
        cmap="terrain",
        vmin=vmin,
        vmax=vmax,
    )

    ax.imshow(
        hillshade,
        extent=extent,
        origin="upper",
        cmap="gray",
        alpha=0.25,
    )

    cb = fig.colorbar(
        dem_plot,
        ax=ax,
        shrink=0.82,
    )
    cb.set_label("Elevation (m)")

    ax.set_title(
        f"{product['product_tag']} — "
        f"{product['geometry']}\nFinal DSM"
    )

    ax.grid(
        True,
        color="white",
        alpha=0.22,
        linewidth=0.5,
        linestyle="--",
    )

    if has_error:
        error, ebounds, _ = (
            _read_dem_preview(
                product["error_image"]
            )
        )

        eextent = [
            ebounds.left,
            ebounds.right,
            ebounds.bottom,
            ebounds.top,
        ]

        eax = axes[0, 1]

        error_plot = eax.imshow(
            error,
            extent=eextent,
            origin="upper",
            cmap="Reds",
            vmin=0,
            vmax=1,
        )

        ecb = fig.colorbar(
            error_plot,
            ax=eax,
            shrink=0.82,
        )
        ecb.set_label(
            "Intersection error (m)"
        )

        eax.set_title(
            "Intersection error"
        )

        eax.grid(
            True,
            color="white",
            alpha=0.22,
            linewidth=0.5,
            linestyle="--",
        )

    fig.suptitle(
        f"{settings.project_name} — "
        f"{product['algorithm']} "
        f"CK{product['correlation_kernel']} "
        f"SK{product['subpixel_kernel']}"
    )

    fig.tight_layout()

    safe_name = (
        f"{product['product_tag']}_"
        f"{settings.project_name}_"
        f"{_safe_algorithm_filename(product['algorithm'])}_"
        f"ck{product['correlation_kernel']}_"
        f"sk{product['subpixel_kernel']}_"
        "final_DSM"
    )

    png = (
        settings.figure_dir
        / f"{safe_name}.png"
    )
    pdf = (
        settings.figure_dir
        / f"{safe_name}.pdf"
    )

    fig.savefig(
        png,
        dpi=200,
        bbox_inches="tight",
    )
    fig.savefig(
        pdf,
        dpi=300,
        bbox_inches="tight",
    )

    return {
        "figure": fig,
        "png": png,
        "pdf": pdf,
    }


def generate_final_dsms(
    settings: ProjectSettings,
    processing: PreProcessingSettings,
    final: FinalProcessingSettings,
    progress_callback=None,
):
    """
    Rasterize every currently selected final point cloud with point2dem.
    """
    # Validate CRS/reference inputs and aligned processing state.
    _final_common_paths(
        settings,
        processing,
    )

    point_clouds = (
        _selected_point_clouds_for_dsm(
            settings,
            processing,
            final,
        )
    )

    final_dsm_dir = (
        _processing_output_dir(settings)
        / "final_dsms"
    )

    final_dsm_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    summary_log = (
        settings.log_dir
        / "final_dsm_generation.log"
    )

    generated = []

    with WorkflowLog(summary_log) as summary:
        try:
            for index, item in enumerate(
                point_clouds,
                start=1,
            ):
                ck = item[
                    "correlation_kernel"
                ]
                sk = item[
                    "subpixel_kernel"
                ]
                product_tag = item[
                    "product_tag"
                ]

                if progress_callback:
                    progress_callback(
                        5
                        + int(
                            (index - 1)
                            / len(point_clouds)
                            * 82
                        ),
                        (
                            f"DSM {product_tag} — "
                            f"{ck}:{sk}"
                        ),
                    )

                algorithm_file_tag = _safe_algorithm_filename(
                    _resolve_final_algorithm(final)["display_name"]
                )

                outname = (
                    f"{settings.project_name}_"
                    f"{algorithm_file_tag}_"
                    f"ck{ck}_sk{sk}"
                )

                dem_name = (
                    f"{product_tag}_"
                    f"{outname}-"
                    f"{final.final_dsm_resolution_m}m"
                )

                dem_prefix = (
                    final_dsm_dir
                    / dem_name
                )

                expected_dem = Path(
                    f"{dem_prefix}-DEM.tif"
                )

                expected_error = Path(
                    f"{dem_prefix}"
                    "-IntersectionErr.tif"
                )

                log_file = (
                    _processing_log_dir(settings)
                    / f"point2dem.{dem_name}.log"
                )

                _prepare_output_prefix(
                    dem_prefix,
                    log_file,
                    settings.overwrite,
                )

                arguments = [
                    "--max-valid-triangulation-error",
                    (
                        final
                        .max_valid_triangulation_error_m
                    ),
                    "--t_srs",
                    f"EPSG:{processing.target_epsg}",
                    "--tr",
                    final.final_dsm_resolution_m,
                    "--threads",
                    final.final_dsm_threads,
                    "--nodata-value",
                    final.final_dsm_nodata,
                    "--tif-compress",
                    final.final_dsm_compression,
                ]

                if final.create_error_image:
                    arguments.append(
                        "--errorimage"
                    )

                arguments.extend(
                    [
                        item["point_cloud"],
                        "-o",
                        dem_prefix,
                    ]
                )

                _run_asp_command(
                    "point2dem",
                    arguments,
                    log_file,
                    _processing_root(settings),
                )

                _require_existing_files(
                    expected_dem
                )

                error_image = None

                if final.create_error_image:
                    _require_existing_files(
                        expected_error
                    )
                    error_image = (
                        expected_error
                    )

                product = {
                    **item,
                    "dem": expected_dem,
                    "error_image": error_image,
                    "log": log_file,
                }

                product["plot"] = (
                    _plot_one_final_dsm(
                        settings,
                        product,
                    )
                )

                generated.append(product)

                summary.write(
                    f"Final DSM: {expected_dem}"
                )

            products_df = pd.DataFrame(
                [
                    {
                        "Product": item[
                            "product_tag"
                        ],
                        "Geometry": item[
                            "geometry"
                        ],
                        "Algorithm": item[
                            "algorithm"
                        ],
                        "CK": item[
                            "correlation_kernel"
                        ],
                        "SK": item[
                            "subpixel_kernel"
                        ],
                        "Point cloud": str(
                            item["point_cloud"]
                        ),
                        "DSM": str(
                            item["dem"]
                        ),
                        "Intersection error": (
                            str(
                                item["error_image"]
                            )
                            if item[
                                "error_image"
                            ] is not None
                            else ""
                        ),
                    }
                    for item in generated
                ]
            )

            csv_path = (
                settings.metadata_dir
                / "final_dsm_products.csv"
            )

            products_df.to_csv(
                csv_path,
                index=False,
            )

            state_path = _save_processing_state(
                settings,
                "final_dsm",
                {
                    "resolution_m": (
                        final.final_dsm_resolution_m
                    ),
                    "max_valid_triangulation_error_m": (
                        final
                        .max_valid_triangulation_error_m
                    ),
                    "create_error_image": (
                        final.create_error_image
                    ),
                    "products": [
                        {
                            "product_tag": item[
                                "product_tag"
                            ],
                            "dsm": str(
                                item["dem"]
                            ),
                            "error_image": (
                                str(
                                    item[
                                        "error_image"
                                    ]
                                )
                                if item[
                                    "error_image"
                                ] is not None
                                else None
                            ),
                        }
                        for item in generated
                    ],
                },
            )

            if progress_callback:
                progress_callback(
                    100,
                    "Final DSM generation completed",
                )

            return {
                "products": generated,
                "products_df": products_df,
                "products_csv": csv_path,
                "summary_log": summary_log,
                "state_path": state_path,
            }

        except Exception:
            summary.write("")
            summary.write("ERROR")
            summary.write(
                traceback.format_exc()
            )
            raise


# ============================================================
# EXTENDED v0.5 INTERFACE
# ============================================================

class FullProjectSetupUI(ProjectSetupUI):
    """
    Keep the v0.5 interface exactly as it was and append the remaining
    processing sections underneath it.
    """

    def __init__(self):
        super().__init__()

        widgets = self.widgets
        style = {"description_width": "205px"}
        wide = widgets.Layout(width="810px")

        self.last_pre_processing = None
        self.last_point_cloud = None
        self.last_final_dsm = None

        # One controller owns the currently running long workflow task.
        # Only one ASP-heavy task may run at a time; this prevents two runs
        # from writing to the same output prefixes concurrently.
        self._execution_control_groups = {}
        self._execution_run_buttons = {}
        self._execution_thread = None
        self._active_execution_scope = None
        self._pending_auto_final_dsm = False
        self._execution_controller = ManagedProcessController(
            state_callback=self._on_execution_controller_state
        )

        # ----------------------------------------------------
        # PRE-PROCESSING INPUTS
        # ----------------------------------------------------
        self.alignment_dem = widgets.Text(
            description="Alignment reference DEM:",
            placeholder="/path/to/alignment_reference_DEM.tif",
            style=style,
            layout=wide,
        )

        self.mapproject_dem = widgets.Text(
            description="Map-projection DEM:",
            placeholder="/path/to/existing_reference_DEM.tif",
            style=style,
            layout=wide,
        )

        # ----------------------------------------------------
        # INTEGRATED REFERENCE DEM SETTINGS
        # ----------------------------------------------------
        # The two widgets above are retained as internal resolved paths so the
        # tested ASP processing functions remain unchanged. Users configure
        # reference topography through the compact section below.
        self.reference_region = widgets.Dropdown(
            options=[
                ("France — mainland", "france"),
                ("Other / global", "global"),
            ],
            value="france",
            description="Country / region:",
            style=style,
            layout=widgets.Layout(width="620px"),
        )

        self.reference_aoi = widgets.Text(
            description="Reference DEM AOI:",
            placeholder="Leave blank to reuse Prepare data AOI",
            style=style,
            layout=wide,
        )

        self.reference_map_source = widgets.Dropdown(
            options=[],
            description="Map DEM source:",
            style=style,
            layout=widgets.Layout(width="680px"),
        )

        self.reference_map_resolution = widgets.FloatText(
            value=50.0,
            description="Map DEM resolution (m):",
            style=style,
        )

        self.reference_existing_map = widgets.Text(
            description="Existing map DEM:",
            placeholder="/path/to/map_projection_DEM.tif",
            style=style,
            layout=wide,
        )

        self.reference_existing_map_convert = widgets.Checkbox(
            value=False,
            description="Convert existing map DEM to ellipsoidal heights",
            indent=False,
        )

        self.reference_alignment_source = widgets.Dropdown(
            options=[],
            description="Alignment reference:",
            style=style,
            layout=widgets.Layout(width="720px"),
        )

        self.reference_alignment_resolution = widgets.FloatText(
            value=1.0,
            description="Alignment DEM res. (m):",
            style=style,
        )

        self.reference_existing_alignment = widgets.Text(
            description="Existing alignment DEM:",
            placeholder="/path/to/alignment_reference_DEM.tif",
            style=style,
            layout=wide,
        )

        self.reference_existing_alignment_convert = widgets.Checkbox(
            value=False,
            description="Convert existing alignment DSM to ellipsoidal heights",
            indent=False,
        )

        # Vertical-reference selectors remain fully editable.  The legacy
        # reference_geoid_model name is retained for the alignment role so
        # existing notebooks/scripts continue to work.
        self.reference_geoid_model = widgets.Dropdown(
            options=[],
            description="Alignment geoid:",
            style=style,
            layout=widgets.Layout(width="690px"),
        )

        self.reference_map_geoid_model = widgets.Dropdown(
            options=[],
            description="Map geoid:",
            style=style,
            layout=widgets.Layout(width="690px"),
        )

        self.reference_split_vertical_models = widgets.Checkbox(
            value=False,
            description="Use separate vertical references",
            indent=False,
            layout=widgets.Layout(width="420px"),
        )

        self.reference_custom_n = widgets.Text(
            description="Alignment custom N:",
            placeholder="/path/to/N_h_minus_H.tif",
            style=style,
            layout=wide,
        )

        self.reference_map_custom_n = widgets.Text(
            description="Map custom N:",
            placeholder="/path/to/N_h_minus_H.tif",
            style=style,
            layout=wide,
        )

        self.reference_global_buffer = widgets.FloatText(
            value=0.05,
            description="Global DEM buffer (deg):",
            style=style,
        )

        self.reference_ign_buffer = widgets.FloatText(
            value=1000.0,
            description="IGN buffer (m):",
            style=style,
        )

        self.reference_ign_workers = widgets.IntText(
            value=2,
            description="IGN download workers:",
            style=style,
        )

        self.reference_dem_box = widgets.VBox()

        self.reference_dem_heading = widgets.HTML(
            value=(
                "<div style='"
                "font-size:18px;"
                "font-weight:700;"
                "color:#17324d;"
                "margin:12px 0 6px 0;"
                "'>"
                "Reference DEM settings"
                "</div>"
            )
        )

        self.reference_dem_settings = widgets.Accordion(
            children=[self.reference_dem_box]
        )
        self.reference_dem_settings.set_title(
            0,
            "Show / hide settings",
        )
        self.reference_dem_settings.selected_index = 0

        self.reference_dem_status = widgets.HTML(
            value=(
                "<div style='margin:5px 0 8px 0;color:#666;'>"
                "Prepare and inspect the reference DEMs and vertical correction before starting bundle adjustment."
                "</div>"
            )
        )

        self.prepare_reference_dems = widgets.Button(
            description="1. Prepare & check reference DEMs",
            button_style="info",
            icon="eye",
            layout=widgets.Layout(width="330px", height="42px"),
        )
        self.reference_dem_progress = widgets.IntProgress(
            value=0, min=0, max=100, description="Ref DEM:",
            style={"description_width": "80px"},
            layout=widgets.Layout(width="720px"),
        )
        self.reference_dem_progress_text = widgets.HTML(
            "<span style='color:#666;'>Waiting.</span>"
        )
        self.reference_dem_qc_outputs = {
            "alignment": widgets.Output(layout=widgets.Layout(width="100%", min_height="120px")),
            "mapproject": widgets.Output(layout=widgets.Layout(width="100%", min_height="120px")),
            "geoid": widgets.Output(layout=widgets.Layout(width="100%", min_height="120px")),
        }
        self.reference_dem_qc_tabs = widgets.Tab(children=[
            self.reference_dem_qc_outputs["alignment"],
            self.reference_dem_qc_outputs["mapproject"],
            self.reference_dem_qc_outputs["geoid"],
        ])
        self.reference_dem_qc_tabs.set_title(0, "Alignment DEM")
        self.reference_dem_qc_tabs.set_title(1, "Map-projection DEM")
        self.reference_dem_qc_tabs.set_title(2, "Geoid / Vertical")
        self.reference_dem_qc_tabs.layout.display = "none"
        self.section_hierarchy_style = widgets.HTML(
            value=(
                "<style>"
                ".widget-accordion .p-Accordion-header, "
                ".widget-accordion .p-Accordion-headerLabel, "
                ".widget-accordion .p-Accordion-header-link, "
                ".jupyter-widgets.widget-accordion .p-Accordion-header, "
                ".jupyter-widgets.widget-accordion .p-Accordion-headerLabel, "
                ".jupyter-widgets.widget-accordion .p-Accordion-header-link, "
                ".lm-AccordionPanel-title, "
                ".lm-AccordionPanel-titleLabel {"
                "font-size:30px !important;"
                "font-weight:700 !important;"
                "line-height:1.5 !important;"
                "}"
                "</style>"
            )
        )


        self.reference_dem_gate_note = widgets.HTML(
            value=(
                "<div style='margin:5px 0 10px 0;padding:8px 10px;"
                "border-left:3px solid #336699;background:#f7f9fc;"
                "color:#555;max-width:850px;font-size:12px;line-height:1.45;'>"
                "<b>QC gate:</b> ASP has not started yet. Each prepared-reference tab keeps its "
                "<b>QC figure + bordered numerical table</b>. Inspect the Alignment DEM, "
                "Map-projection DEM, and Geoid / Vertical tabs. "
                "Only then run step 2. If a DEM is wrong, change the reference "
                "settings and prepare again."
                "</div>"
            )
        )
        self._reference_dem_ready = False

        # Keep the complete Reference DEM workflow inside one collapsible section:
        # controls, preparation status, execution controls, progress, QC gate,
        # and all QC figure/table tabs disappear together when the section is hidden.
        self.reference_dem_section_content = widgets.VBox([
            self.reference_dem_box,
            self.reference_dem_status,
            self.prepare_reference_dems,
            self.reference_dem_progress,
            self.reference_dem_progress_text,
            self.reference_dem_gate_note,
            self.reference_dem_qc_tabs,
        ])
        self.reference_dem_settings.children = (self.reference_dem_section_content,)
        self.reference_dem_settings.set_title(0, "Reference DEM settings — show / hide")

        self.camera_model = widgets.Dropdown(
            options=[
                ("RPC — RPC XML (default)", "rpc"),
                ("Pléiades exact linescan — DIM XML", "pleiades"),
            ],
            value="rpc",
            description="Camera model:",
            style=style,
            layout=widgets.Layout(width="520px"),
        )

        self.final_camera_model = widgets.Dropdown(
            options=self.camera_model.options,
            value="rpc",
            description="Camera model:",
            style=style,
            disabled=True,
            layout=widgets.Layout(width="520px"),
        )

        self.camera_model_note = widgets.HTML()

        self.target_epsg = widgets.IntText(
            value=32632,
            description="Target CRS (EPSG):",
            style=style,
        )

        self.map_resolution = widgets.FloatText(
            value=0.5,
            description="Map image resolution:",
            style=style,
        )

        self.preliminary_pair = widgets.Dropdown(
            options=[
                (
                    "AC — Forward–Backward",
                    "AC",
                ),
                (
                    "AB — Forward–Middle",
                    "AB",
                ),
                (
                    "BC — Middle–Backward",
                    "BC",
                ),
            ],
            value="AC",
            description="Preliminary pair:",
            style=style,
        )

        self.ba_cost_function = widgets.Combobox(
            options=["Cauchy", "PseudoHuber", "Huber", "L1", "L2"],
            value="Cauchy",
            ensure_option=False,
            placeholder="Leave blank = ASP default (Cauchy), or type a custom ASP value",
            description="BA cost function:",
            style=style,
            layout=widgets.Layout(width="620px"),
        )

        self.ba_robust_threshold = widgets.Text(
            value="2.0",
            placeholder="Leave blank = ASP default (0.5)",
            description="BA robust threshold:",
            style=style,
            layout=widgets.Layout(width="620px"),
        )

        self.ba_max_iterations = widgets.Text(
            value="500",
            placeholder="Leave blank = ASP default (1000)",
            description="BA max iterations:",
            style=style,
            layout=widgets.Layout(width="620px"),
        )

        # ----------------------------------------------------
        # PRELIMINARY STEREO CONTROLS
        # ----------------------------------------------------
        # The defaults reproduce the original tested preprocessing:
        # asp_bm with CK 35 / SK 45, cost mode 2.
        self.prelim_algorithm = widgets.Dropdown(
            options=[
                ("BM — asp_bm", "BM"),
                ("SGM — asp_sgm", "SGM"),
                ("MGM — asp_mgm", "MGM"),
                ("Other / custom algorithm", "CUSTOM"),
            ],
            value="BM",
            description="Prelim algorithm:",
            style=style,
            layout=widgets.Layout(width="520px"),
        )

        self.prelim_custom_algorithm = widgets.Text(
            value="",
            description="Custom prelim alg.:",
            placeholder="Enter ASP --stereo-algorithm value",
            style=style,
            layout=wide,
        )

        self.prelim_kernel_selector = widgets.Dropdown(
            options=[],
            description="Prelim CK : SK:",
            style=style,
            layout=widgets.Layout(width="520px"),
        )

        self.prelim_custom_kernel = widgets.Text(
            value="",
            description="Custom prelim CK:SK:",
            placeholder="Example: 11:23",
            style=style,
            layout=wide,
        )

        self.prelim_cost_mode = widgets.IntText(
            value=2,
            description="Prelim cost mode:",
            style=style,
            disabled=True,
        )

        self.prelim_xcorr_threshold = widgets.FloatText(
            value=2.0,
            description="Prelim xcorr threshold:",
            style=style,
        )

        self.prelim_corr_memory = widgets.IntText(
            value=10240,
            description="Prelim corr memory:",
            style=style,
        )

        self.prelim_corr_tile_size = widgets.IntText(
            value=3200,
            description="Prelim corr tile size:",
            style=style,
        )

        self.prelim_subpixel_mode = widgets.IntText(
            value=2,
            description="Prelim subpixel mode:",
            style=style,
        )

        self.pc_align_max_displacement = (
            widgets.FloatText(
                value=250.0,
                description="Max displacement (m):",
                style=style,
            )
        )

        self.pc_align_iterations = (
            widgets.IntText(
                value=100,
                description="pc_align iterations:",
                style=style,
            )
        )

        self.prelim_dem_resolution = widgets.FloatText(
            value=1.0,
            description="Prelim DSM resolution:",
            style=style,
        )

        self.prelim_dem_nodata = widgets.FloatText(
            value=-9999.0,
            description="Prelim DSM nodata:",
            style=style,
        )

        self.mapproject_threads = widgets.IntText(
            value=18,
            description="Mapproject threads:",
            style=style,
        )

        self.run_camera_test = widgets.Button(
            description="Compare RPC vs DIM (cam_test)",
            button_style="info",
            icon="exchange-alt",
            layout=widgets.Layout(width="300px", height="38px"),
        )
        self.camera_test_execution_controls = self._make_execution_controls(
            "camera_test", self.run_camera_test, "DIM vs RPC comparison"
        )
        self.camera_test_summary = widgets.HTML()
        self.camera_test_summary_details = widgets.Accordion(children=[self.camera_test_summary])
        self.camera_test_summary_details.set_title(0, "Run details / processing summary")
        self.camera_test_summary_details.selected_index = None
        self.camera_test_output = widgets.HTML(
            value="<div style='padding:10px 0;color:#777;'>No comparison results yet.</div>",
            layout=widgets.Layout(width="100%")
        )

        self.camera_comparison_panel = widgets.VBox([
            widgets.HTML(
                "<div style='margin:8px 0 10px 0;padding:10px 12px;"
                "border-left:4px solid #7b5fa6;background:#faf8ff;'>"
                "<div style='font-size:16px;font-weight:700;color:#3b2d55;margin-bottom:3px;'>"
                "Compared geometry — exact DIM vs RPC</div>"
                "<span style='color:#666;font-size:12px;'>"
                "Runs ASP <code>cam_test</code> on each normalized full image using the "
                "same command structure as the external comparison script: DIM exact camera "
                "as cam1 and RPC as cam2. No height or sampling override is imposed by the workflow."
                "</span></div>"
            ),
            self.camera_test_execution_controls,
            widgets.HTML(
                "<div style='margin:10px 0;padding:10px 12px;"
                "border-left:4px solid #d28b00;background:#fffaf0;"
                "font-size:12px;line-height:1.5;'>"
                "<b>Interpretation note:</b> Large DIM–RPC pixel discrepancies mean "
                "the RPC approximation does not reproduce the exact line-scan geometry well; "
                "for accuracy-sensitive processing, prefer the DIM camera. When discrepancies "
                "are small, RPC is usually the simpler and faster practical choice. "
                "There is no universal pixel threshold: interpret the values relative to "
                "image resolution, terrain relief, and the required DSM accuracy."
                "</div>"
            ),
            self.camera_test_summary_details,
            widgets.HTML(
                "<div style='margin:12px 0 6px 0;padding:8px 10px;"
                "border-bottom:2px solid #7b5fa6;font-size:14px;font-weight:700;'>"
                "Comparison results — numerical table + figure</div>"
            ),
            self.camera_test_output,
        ])

        self.target_epsg_row = self._row(
            self.target_epsg,
            "Target projected CRS used by the ASP outputs. "
            "This value is site-dependent; change it for the study area. "
            "The supplied reference DEMs should be prepared/reprojected "
            "consistently with this target CRS before processing.",
        )

        self.crs_information_note = widgets.HTML(
            value=(
                "<div style='margin:3px 0 12px 205px;"
                "padding:9px 11px;border-left:3px solid #336699;"
                "background:#f7f9fc;color:#555;max-width:760px;"
                "font-size:12px;line-height:1.45;'>"
                "<b>CRS / UTM zone:</b> the appropriate projected CRS "
                "depends on the geographic location of the study area. "
                "Confirm the EPSG code before processing. "
                "<a href='https://epsg.io/' target='_blank' "
                "rel='noopener noreferrer'>Check CRS / UTM information on EPSG.io ↗</a>"
                "<br>"
                "Use the same target CRS for the stereo-derived products "
                "and the prepared reference DEMs used for alignment and map projection."
                "</div>"
            )
        )

        self.prelim_algorithm_row = self._row(
            self.prelim_algorithm,
            "Preliminary stereo algorithm. The tested workflow default is "
            "BM / asp_bm.",
        )

        self.prelim_custom_algorithm_row = self._row(
            self.prelim_custom_algorithm,
            "Shown only for Other/custom. Enter the exact ASP "
            "--stereo-algorithm value.",
        )

        self.prelim_kernel_row = self._row(
            self.prelim_kernel_selector,
            "Preliminary linked correlation/subpixel kernel pair. "
            "The original tested BM default is CK 35 / SK 45.",
        )

        self.prelim_custom_kernel_row = self._row(
            self.prelim_custom_kernel,
            "Shown only for Other/custom CK:SK. Enter one linked pair, "
            "for example 11:23.",
        )

        self.prelim_cost_mode_row = self._row(
            self.prelim_cost_mode,
            "Automatic preset: BM = 2; SGM/MGM = 4. Editable only for "
            "a custom preliminary algorithm.",
        )

        self.preprocess_advanced_box = widgets.VBox()
        self.preprocess_advanced_heading = widgets.HTML(
            value=(
                "<div style='"
                "font-size:18px;"
                "font-weight:700;"
                "color:#17324d;"
                "margin:12px 0 6px 0;"
                "'>"
                "Advanced pre-processing settings"
                "</div>"
            )
        )
        self.preprocess_advanced = (
            widgets.Accordion(
                children=[
                    self.preprocess_advanced_box
                ]
            )
        )

        self.preprocess_advanced.set_title(
            0,
            "Show / hide settings",
        )

        self.run_preprocessing = widgets.Button(
            description="2. Run ASP pre-processing",
            button_style="warning",
            icon="cogs",
            disabled=True,
            layout=widgets.Layout(width="290px", height="42px"),
        )
        self.preprocess_execution_controls = self._make_execution_controls(
            "preprocessing", self.run_preprocessing, "ASP pre-processing"
        )

        self.preprocess_progress = (
            widgets.IntProgress(
                value=0,
                min=0,
                max=100,
                description="Progress:",
                style={
                    "description_width": "80px"
                },
                layout=widgets.Layout(
                    width="720px"
                ),
            )
        )

        self.preprocess_progress_text = (
            widgets.HTML(
                "<span style='color:#666;'>"
                "Waiting.</span>"
            )
        )

        self.preprocess_summary = widgets.HTML()
        self.preprocess_summary_details = widgets.Accordion(children=[self.preprocess_summary])
        self.preprocess_summary_details.set_title(0, "Run details / processing summary")
        self.preprocess_summary_details.selected_index = None

        self._preprocess_stage_order = [
            "bundle_adjustment",
            "preliminary_stereo",
            "preliminary_dem",
            "lidar_alignment",
            "camera_transform",
            "map_projection",
        ]

        self._preprocess_stage_labels = {
            "bundle_adjustment": "Bundle adjustment",
            "preliminary_stereo": "Prelim stereo",
            "preliminary_dem": "Prelim DSM",
            "lidar_alignment": "Reference alignment",
            "camera_transform": "Camera transform",
            "map_projection": "Map projection",
        }

        self.preprocess_run_mode = widgets.ToggleButtons(
            options=[
                ("Run all pre-processing", "all"),
                ("Run one step", "single"),
            ],
            value="all",
            description="Run mode:",
            style=style,
        )

        self.preprocess_single_stage = widgets.Dropdown(
            options=[
                (self._preprocess_stage_labels[stage], stage)
                for stage in self._preprocess_stage_order
            ],
            value="bundle_adjustment",
            description="Step:",
            style=style,
            disabled=True,
        )

        self.preprocess_run_note = widgets.HTML(
            "<div style='margin:4px 0 8px 205px;color:#666;max-width:760px;"
            "font-size:12px;line-height:1.45;'>"
            "<b>Default:</b> run the complete six-step chain. Switch to "
            "<b>Run one step</b> to reuse successful prerequisite outputs already present on disk; "
            "the prerequisite does not need to have been run again in the current notebook session. "
            "Earlier successful step results remain visible. "
            "If a failed step left partial files, enable <b>Overwrite existing outputs</b> "
            "before rerunning that step.<br><b>Important:</b> changing an upstream "
            "step does not automatically rebuild downstream products; rerun any "
            "dependent downstream steps that must reflect the new result.<br>"
            "<b>Execution controls:</b> Pause/Resume continues the same active ASP "
            "process on Linux/POSIX; Stop cancels the active process tree. After Stop, "
            "the Run button is restored so the selected step can be started again."
            "</div>"
        )

        # Text/tables are rendered through direct HTML widgets rather than
        # IPython display capture. This is robust when ASP runs in the managed
        # background worker (Output widgets can otherwise appear blank even
        # though the stage completed successfully). Rich figures still use a
        # small Output widget below the HTML content.
        self._preprocess_stage_html = {
            stage: widgets.HTML(
                "<div style='padding:12px;color:#777;'>Waiting for this processing step.</div>"
            )
            for stage in self._preprocess_stage_order
        }

        self._preprocess_stage_outputs = {
            stage: widgets.Output(
                layout=widgets.Layout(
                    width="100%",
                    min_height="0px",
                )
            )
            for stage in self._preprocess_stage_order
        }

        self._preprocess_stage_containers = {
            stage: widgets.VBox([
                self._preprocess_stage_html[stage],
                self._preprocess_stage_outputs[stage],
            ])
            for stage in self._preprocess_stage_order
        }

        self._preprocess_stage_status = {
            stage: "waiting" for stage in self._preprocess_stage_order
        }

        self.preprocess_stage_tabs = widgets.Tab(
            children=[
                self._preprocess_stage_containers[stage]
                for stage in self._preprocess_stage_order
            ]
        )

        for index, stage in enumerate(
            self._preprocess_stage_order
        ):
            self.preprocess_stage_tabs.set_title(
                index,
                self._preprocess_stage_labels[stage],
            )

        self.preprocess_results = widgets.VBox(
            [self.preprocess_stage_tabs]
        )

        # ----------------------------------------------------
        # POINT CLOUD INPUTS
        # ----------------------------------------------------
        self.final_mode = widgets.Dropdown(
            options=[
                (
                    "Single pair(s)",
                    "single",
                ),
                (
                    "Ordered three-image configuration(s)",
                    "dual",
                ),
                (
                    "Tri — AB + AC + BC then merge",
                    "tri",
                ),
            ],
            value="dual",
            description="Stereo mode:",
            style=style,
        )

        self.single_pair_selector = (
            widgets.SelectMultiple(
                options=[
                    (
                        "AB — FM",
                        "AB",
                    ),
                    (
                        "AC — FB",
                        "AC",
                    ),
                    (
                        "BC — MB",
                        "BC",
                    ),
                ],
                value=("AC",),
                description="Stereo pair(s):",
                style=style,
                layout=widgets.Layout(
                    width="600px",
                    height="95px",
                ),
            )
        )

        self.dual_selector = (
            widgets.SelectMultiple(
                options=[
                    (
                        "ABC — FMB",
                        "ABC",
                    ),
                    (
                        "BAC — MFB",
                        "BAC",
                    ),
                    (
                        "CAB — BFM",
                        "CAB",
                    ),
                ],
                value=("CAB",),
                description="3-image config(s):",
                style=style,
                layout=widgets.Layout(
                    width="600px",
                    height="95px",
                ),
            )
        )

        self.tri_mode_info = widgets.HTML(
            "<div style='margin-left:205px;padding:8px 10px;"
            "border-left:3px solid #1976d2;background:#f5f9ff;"
            "max-width:720px;'>"
            "<b>Tri mode:</b> AB (FM), AC (FB), and BC (MB) are run "
            "automatically, then their point clouds are merged with "
            "<code>pc_merge</code>. No manual pair selection is required."
            "</div>"
        )


        self.final_algorithm = widgets.Dropdown(
            options=[
                ("BM — asp_bm", "BM"),
                ("SGM — asp_sgm", "SGM"),
                ("MGM — asp_mgm", "MGM"),
                ("Other / custom algorithm", "CUSTOM"),
            ],
            value="MGM",
            description="Algorithm:",
            style=style,
            layout=widgets.Layout(width="520px"),
        )

        self.custom_algorithm_name = widgets.Text(
            value="",
            description="Custom algorithm:",
            placeholder="Enter ASP --stereo-algorithm value",
            style=style,
            layout=wide,
        )

        # Preset algorithms set this automatically.
        # It becomes editable only for the CUSTOM option.
        self.custom_cost_mode = widgets.IntText(
            value=4,
            description="Cost mode:",
            style=style,
            disabled=True,
        )

        self.final_xcorr_threshold = widgets.FloatText(
            value=2.0,
            description="Xcorr threshold:",
            style=style,
        )

        self.final_corr_memory = widgets.IntText(
            value=10240,
            description="Corr memory (MB):",
            style=style,
        )

        self.final_corr_tile_size = widgets.IntText(
            value=3200,
            description="Corr tile size:",
            style=style,
        )

        self.final_subpixel_mode = widgets.IntText(
            value=2,
            description="Subpixel mode:",
            style=style,
        )

        self.kernel_selector = (
            widgets.SelectMultiple(
                options=[
                    (
                        "5 : 9",
                        (5, 9),
                    ),
                    (
                        "7 : 15",
                        (7, 15),
                    ),
                    (
                        "9 : 21",
                        (9, 21),
                    ),
                ],
                value=((9, 21),),
                description="CK : SK pair(s):",
                style=style,
                layout=widgets.Layout(
                    width="520px",
                    height="135px",
                ),
            )
        )

        self.custom_kernel_pairs = widgets.Text(
            value="",
            description="Additional CK:SK:",
            placeholder="Example: 11:23,13:27",
            style=style,
            layout=wide,
        )

        self.auto_generate_dsm = (
            widgets.Checkbox(
                value=True,
                description=(
                    "Generate final DSM automatically "
                    "after point-cloud reconstruction"
                ),
                indent=False,
            )
        )

        self.run_point_cloud = widgets.Button(
            description="Run point-cloud reconstruction",
            button_style="info",
            icon="cube",
            layout=widgets.Layout(
                width="310px",
                height="42px",
            ),
        )
        self.point_cloud_execution_controls = self._make_execution_controls(
            "point_cloud", self.run_point_cloud, "Point-cloud reconstruction"
        )

        self.point_cloud_progress = (
            widgets.IntProgress(
                value=0,
                min=0,
                max=100,
                description="Progress:",
                style={
                    "description_width": "80px"
                },
                layout=widgets.Layout(
                    width="720px"
                ),
            )
        )

        self.point_cloud_progress_text = (
            widgets.HTML(
                "<span style='color:#666;'>"
                "Waiting.</span>"
            )
        )

        self.point_cloud_summary = widgets.HTML()
        self.point_cloud_summary_details = widgets.Accordion(children=[self.point_cloud_summary])
        self.point_cloud_summary_details.set_title(0, "Run details / processing summary")
        self.point_cloud_summary_details.selected_index = None
        self.point_cloud_results = widgets.VBox()

        # ----------------------------------------------------
        # FINAL DSM INPUTS
        # ----------------------------------------------------
        self.final_dsm_resolution = (
            widgets.FloatText(
                value=1.0,
                description="DSM resolution (m):",
                style=style,
            )
        )

        self.max_triangulation_error = (
            widgets.FloatText(
                value=1.0,
                description="Max triangulation error:",
                style=style,
            )
        )

        self.create_error_image = (
            widgets.Checkbox(
                value=True,
                description=(
                    "Create intersection-error image"
                ),
                indent=False,
            )
        )

        self.final_dsm_threads = widgets.IntText(
            value=0,
            description="point2dem threads:",
            style=style,
        )

        self.pc_merge_threads = widgets.IntText(
            value=18,
            description="pc_merge threads:",
            style=style,
        )

        self.final_advanced = (
            widgets.Accordion(
                children=[
                    widgets.VBox(
                        [
                            self._row(
                                self.final_camera_model,
                                "Inherited from ASP pre-processing. The same camera model/session "
                                "and XML files are reused by final parallel_stereo; this value "
                                "cannot be changed independently.",
                            ),
                            self._row(
                                self.custom_cost_mode,
                                "Automatically set to 2 for BM and 4 for "
                                "SGM/MGM. It becomes editable only when "
                                "'Other / custom algorithm' is selected.",
                            ),
                            self._row(
                                self.final_xcorr_threshold,
                                "Final parallel_stereo --xcorr-threshold. "
                                "Tested workflow default: 2.0.",
                            ),
                            self._row(
                                self.final_corr_memory,
                                "Final correlation memory limit in MB. "
                                "Tested workflow default: 10240.",
                            ),
                            self._row(
                                self.final_corr_tile_size,
                                "Final correlation tile size. "
                                "Tested workflow default: 3200.",
                            ),
                            self._row(
                                self.final_subpixel_mode,
                                "Final ASP subpixel mode. "
                                "Tested workflow default: 2.",
                            ),
                            self._row(
                                self.pc_merge_threads,
                                "Used only in Tri mode. "
                                "The original workflow passes this value "
                                "to pc_merge.",
                            ),
                            self._row(
                                self.final_dsm_threads,
                                "The final point2dem thread setting. "
                                "0 lets ASP choose.",
                            ),
                        ]
                    )
                ]
            )
        )

        self.final_advanced.set_title(
            0,
            "Advanced final-processing settings",
        )

        self.run_final_dsm = widgets.Button(
            description="Generate final DSM only",
            button_style="success",
            icon="map",
            layout=widgets.Layout(
                width="270px",
                height="42px",
            ),
        )
        self.final_dsm_execution_controls = self._make_execution_controls(
            "final_dsm", self.run_final_dsm, "Final DSM generation"
        )

        self.final_dsm_progress = (
            widgets.IntProgress(
                value=0,
                min=0,
                max=100,
                description="Progress:",
                style={
                    "description_width": "80px"
                },
                layout=widgets.Layout(
                    width="720px"
                ),
            )
        )

        self.final_dsm_progress_text = (
            widgets.HTML(
                "<span style='color:#666;'>"
                "Waiting.</span>"
            )
        )

        self.final_dsm_summary = widgets.HTML()
        self.final_dsm_summary_details = widgets.Accordion(children=[self.final_dsm_summary])
        self.final_dsm_summary_details.set_title(0, "Run details / processing summary")
        self.final_dsm_summary_details.selected_index = None
        self.final_dsm_results = widgets.VBox()

        # ----------------------------------------------------
        # OPTIONAL CO-REGISTRATION HANDOFF
        # ----------------------------------------------------
        self.open_coregistration_notebook = widgets.Button(
            description="Open optional co-registration notebook",
            button_style="info",
            icon="external-link",
            layout=widgets.Layout(width="330px", height="42px"),
        )
        self.coregistration_status = widgets.HTML(
            "<span style='color:#666;font-size:12px;'>Co-registration is an optional post-processing step outside ASP.</span>"
        )
        self.coregistration_output = widgets.Output()

        # ----------------------------------------------------
        # HELP ROWS
        # ----------------------------------------------------
        preprocessing_rows = [
            self.section_hierarchy_style,
            self.reference_dem_heading,
            self.reference_dem_settings,
            widgets.HTML(
                "<hr style='margin:14px 0 12px 0;'>"
                "<div style='font-size:18px;font-weight:700;color:#17324d;"
                "margin:12px 0 8px 0;'>ASP pre-processing execution</div>"
            ),
            self._row(
                self.preprocess_run_mode,
                "Run the complete chain by default, or rerun only one selected "
                "stage while reusing successful prerequisite outputs on disk.",
            ),
            self._row(
                self.preprocess_single_stage,
                "Enabled in Run one step mode. Select the exact failed or "
                "experimental stage you want to execute.",
            ),
            self.preprocess_run_note,
        ]

        self.final_mode_row = self._row(
            self.final_mode,
            "Single runs selected 2-image pairs. "
            "Ordered three-image runs selected ordered A/B/C configurations. "
            "Tri automatically runs AB, AC and BC and then pc_merge.",
        )

        self.single_pair_row = self._row(
            self.single_pair_selector,
            "For a tri-stereo acquisition, Single mode can run one or more "
            "of AB (FM), AC (FB), and BC (MB). For a true two-image stereo "
            "acquisition, only AB exists and it is selected automatically.",
        )

        self.dual_selector_row = self._row(
            self.dual_selector,
            "Used only for an A/B/C tri-stereo acquisition in ordered "
            "three-image mode. Available tested configurations are "
            "ABC (FMB), BAC (MFB), and CAB (BFM).",
        )

        self.final_algorithm_row = self._row(
            self.final_algorithm,
            "Choose BM, SGM, MGM, or Other/custom. "
            "BM automatically uses cost mode 2. "
            "SGM and MGM automatically use cost mode 4.",
        )

        self.custom_algorithm_row = self._row(
            self.custom_algorithm_name,
            "Shown only when 'Other / custom algorithm' is selected. "
            "Enter the exact ASP --stereo-algorithm value here. "
            "The cost mode and other advanced parameters then become "
            "user-controlled.",
        )

        self.kernel_selector_row = self._row(
            self.kernel_selector,
            "Tested CK:SK presets for the selected algorithm. "
            "You can select one or several. Select "
            "'Other / custom CK:SK → enter below' for a value that "
            "is not listed.",
        )

        self.custom_kernel_row = self._row(
            self.custom_kernel_pairs,
            "This field appears only when "
            "'Other / custom CK:SK' is selected above. "
            "Enter custom linked kernel pairs here. "
            "Examples: 11:23 or 11:23,13:27. "
            "They are passed as --corr-kernel CK CK and "
            "--subpixel-kernel SK SK.",
        )

        self.auto_dsm_row = self._row(
            self.auto_generate_dsm,
            "Keeps point-cloud reconstruction and DSM rasterization as "
            "separate stages while allowing automatic continuation.",
        )

        # Dynamic container: this is rebuilt whenever Stereo mode,
        # acquisition type, or algorithm changes.
        self.point_cloud_controls_box = widgets.VBox()

        # Kept as named rows rather than positional indexes to avoid
        # the visibility bug seen in v0.5.3.
        point_cloud_rows = [
            self.final_mode_row,
            self.single_pair_row,
            self.dual_selector_row,
            self.final_algorithm_row,
            self.custom_algorithm_row,
            self.kernel_selector_row,
            self.custom_kernel_row,
            self.auto_dsm_row,
        ]

        final_dsm_rows = [
            self._row(
                self.final_dsm_resolution,
                "Final point2dem resolution. "
                "The uploaded tested run uses 1.0 m.",
            ),
            self._row(
                self.max_triangulation_error,
                "Maximum valid triangulation error. "
                "The uploaded tested final run uses 1.0 m.",
            ),
            self._row(
                self.create_error_image,
                "Equivalent to point2dem --errorimage.",
            ),
        ]

        self._processing_rows = (
            preprocessing_rows
        )
        self._point_cloud_rows = (
            point_cloud_rows
        )
        self._final_dsm_rows = (
            final_dsm_rows
        )

        # ----------------------------------------------------
        # EVENTS
        # ----------------------------------------------------
        self.prepare_reference_dems.on_click(
            self._on_prepare_reference_dems
        )

        self.run_preprocessing.on_click(
            self._on_pre_processing
        )

        self.run_camera_test.on_click(
            self._on_camera_test
        )

        self.preprocess_run_mode.observe(
            self._on_preprocess_run_mode_change,
            names="value",
        )
        self.preprocess_single_stage.observe(
            self._on_preprocess_single_stage_change,
            names="value",
        )

        self.run_point_cloud.on_click(
            self._on_point_cloud
        )

        self.run_final_dsm.on_click(
            self._on_final_dsm
        )
        self.open_coregistration_notebook.on_click(
            self._on_open_coregistration_notebook
        )

        # Preliminary-stereo controls are independent from the final stereo
        # controls. They only affect the preliminary alignment stereo run.
        self.camera_model.observe(
            self._on_camera_model_change,
            names="value",
        )

        self.platform.observe(
            self._on_camera_model_context_change,
            names="value",
        )

        self.crop_enabled.observe(
            self._on_camera_model_context_change,
            names="value",
        )

        self.prelim_algorithm.observe(
            self._on_prelim_algorithm_change,
            names="value",
        )

        self.prelim_kernel_selector.observe(
            self._on_prelim_kernel_change,
            names="value",
        )

        # Dedicated callbacks keep acquisition, geometry mode, and algorithm
        # updates independent. This avoids nested observer calls when dropdown
        # option lists are refreshed.
        self.final_mode.observe(
            self._on_final_mode_change,
            names="value",
        )

        self.final_algorithm.observe(
            self._on_final_algorithm_change,
            names="value",
        )

        self.kernel_selector.observe(
            self._on_kernel_selection_change,
            names="value",
        )

        self.acquisition_mode.observe(
            self._on_acquisition_mode_change,
            names="value",
        )

        # Reference-DEM controls only affect reference preparation.
        # They do not modify ASP stereo / point-cloud configuration.
        #
        # Guard widget initialization/update transactions so changing Dropdown
        # options/values cannot recursively fire nested Reference DEM callbacks.
        self._updating_reference_controls = False

        self.reference_region.observe(
            self._on_reference_region_change,
            names="value",
        )
        self.reference_map_source.observe(
            self._on_reference_map_source_change,
            names="value",
        )
        self.reference_alignment_source.observe(
            self._on_reference_alignment_source_change,
            names="value",
        )
        self.reference_alignment_resolution.observe(
            self._sync_prelim_dem_resolution_from_alignment,
            names="value",
        )
        self.reference_geoid_model.observe(
            self._on_reference_alignment_geoid_change,
            names="value",
        )
        self.reference_map_geoid_model.observe(
            self._rebuild_reference_dem_controls,
            names="value",
        )
        self.reference_split_vertical_models.observe(
            self._on_reference_split_vertical_change,
            names="value",
        )
        self.reference_existing_map_convert.observe(
            self._rebuild_reference_dem_controls,
            names="value",
        )
        self.reference_existing_alignment_convert.observe(
            self._rebuild_reference_dem_controls,
            names="value",
        )

        for _ref_widget in (
            self.reference_region, self.reference_aoi, self.reference_map_source,
            self.reference_map_resolution, self.reference_existing_map,
            self.reference_existing_map_convert, self.reference_alignment_source,
            self.reference_alignment_resolution, self.reference_existing_alignment,
            self.reference_existing_alignment_convert, self.reference_geoid_model,
            self.reference_map_geoid_model, self.reference_split_vertical_models,
            self.reference_custom_n,
            self.reference_map_custom_n, self.reference_global_buffer,
            self.reference_ign_buffer, self.reference_ign_workers, self.target_epsg,
        ):
            _ref_widget.observe(
                self._invalidate_reference_dem_preparation, names="value"
            )

        self._configure_reference_dem_controls()
        self._sync_prelim_dem_resolution_from_alignment()

        # Initialize shared camera-model controls first.
        self._sync_camera_model_controls()

        # Initialize advanced preliminary-stereo controls first.
        self._apply_prelim_algorithm_selection()
        self._rebuild_preprocess_advanced_controls()

        # Initialize the point-cloud section once, in a deterministic order.
        self._configure_geometry_controls()
        self._apply_algorithm_selection()
        self._rebuild_point_cloud_controls()

        # ----------------------------------------------------
        # APPEND TO THE ORIGINAL v0.5 CONTAINER
        # ----------------------------------------------------
        extra_children = [
            widgets.HTML("<hr><div style='margin:12px 0 10px 0;padding:12px 14px;border-left:6px solid #1976d2;background:#eef5fb;border-radius:3px;'><div style='font-size:24px;font-weight:700;color:#17324d;'>Pre-processing</div><div style='color:#5f6b76;font-size:13px;margin-top:5px;line-height:1.5;'>Prepare and visually check the alignment and map-projection references, then run bundle adjustment → preliminary stereo → preliminary DSM → reference alignment → camera transform → map projection.</div></div>"),
            *preprocessing_rows,
            self.preprocess_advanced,
            widgets.HTML("<br>"),
            self.preprocess_execution_controls,
            self.preprocess_progress,
            self.preprocess_progress_text,
            self.preprocess_summary_details,
            self.preprocess_results,

            widgets.HTML("<hr><div style='margin:12px 0 10px 0;padding:12px 14px;border-left:6px solid #1976d2;background:#eef5fb;border-radius:3px;'><div style='font-size:20px;font-weight:700;color:#17324d;'>Point cloud</div><div style='color:#5f6b76;font-size:13px;margin-top:5px;line-height:1.5;'>Run the selected final stereo geometry with parallel_stereo. In Tri mode, AB, AC and BC are merged with pc_merge.</div></div>"),
            self.point_cloud_controls_box,
            self.final_advanced,
            widgets.HTML("<br>"),
            self.point_cloud_execution_controls,
            self.point_cloud_progress,
            self.point_cloud_progress_text,
            self.point_cloud_summary_details,
            self.point_cloud_results,

            widgets.HTML("<hr><div style='margin:12px 0 10px 0;padding:12px 14px;border-left:6px solid #1976d2;background:#eef5fb;border-radius:3px;'><div style='font-size:20px;font-weight:700;color:#17324d;'>Final DSM</div><div style='color:#5f6b76;font-size:13px;margin-top:5px;line-height:1.5;'>Rasterize the selected point cloud(s) with point2dem. This can be rerun without repeating the expensive stereo correlation.</div></div>"),
            *final_dsm_rows,
            widgets.HTML("<br>"),
            self.final_dsm_execution_controls,
            self.final_dsm_progress,
            self.final_dsm_progress_text,
            self.final_dsm_summary_details,
            self.final_dsm_results,

            widgets.HTML("<hr><div style='margin:12px 0 10px 0;padding:12px 14px;border-left:6px solid #6a3d9a;background:#f7f2fb;border-radius:3px;'><div style='font-size:20px;font-weight:700;color:#4a2768;'>6. Optional co-registration</div><div style='color:#5f6b76;font-size:13px;margin-top:5px;line-height:1.5;'>Co-registration is intentionally kept outside ASP as the final optional workflow step. Open the supplied xDEM notebook only when an external reference/stable-area correction is required.</div></div>"),
            self.open_coregistration_notebook,
            self.coregistration_status,
            self.coregistration_output,

            widgets.HTML("<hr><div style='margin:12px 0 10px 0;padding:12px 14px;border-left:6px solid #1976d2;background:#eef5fb;border-radius:3px;'><div style='font-size:20px;font-weight:700;color:#17324d;'>Runtime summary</div><div style='color:#5f6b76;font-size:13px;margin-top:5px;line-height:1.5;'>Wall-clock runtime is recorded automatically when each workflow stage completes. Rerunning a stage updates the same row, so the table always represents the latest successful run of each step.</div></div>"),
            self.runtime_summary_table,
            self.runtime_summary_path,
        ]

        self.container.children = tuple(
            list(self.container.children)
            + extra_children
        )

        # Base-class startup may already have restored the last project inputs.
        # Once the full processing controls exist, restore reusable reference/
        # processing paths as well so the user can continue directly.
        if self.existing_project_dir.value.strip():
            try:
                _loaded = load_project_config(self.existing_project_dir.value.strip())
                self._restore_processing_state_from_project(_loaded)
            except Exception:
                pass

    def _clear_downstream_project_state(self):
        """Clear project-specific downstream UI state without running ASP."""
        from IPython.display import clear_output

        self._reference_dem_ready = False

        # A new-project reset must also clear the complete reference-DEM panel,
        # not only the resolved paths consumed by ASP.  Keep the scientific
        # defaults, but remove every project-specific AOI/path/QC result.
        if hasattr(self, "reference_region"):
            self._updating_reference_controls = True
            try:
                self.reference_region.value = "france"
                self.reference_aoi.value = ""
                self.reference_existing_map.value = ""
                self.reference_existing_alignment.value = ""
                self.reference_existing_map_convert.value = False
                self.reference_existing_alignment_convert.value = False
                self.reference_split_vertical_models.value = False
                self.reference_custom_n.value = ""
                self.reference_map_custom_n.value = ""
                self.reference_global_buffer.value = 0.05
                self.reference_ign_buffer.value = 1000.0
                self.reference_ign_workers.value = 2
                # Explicitly restore the scientific defaults as well as clearing
                # project-specific paths.  This prevents a loaded project's DEM
                # source/resolution/geoid choices from surviving Start new project.
                self.reference_map_source.options = self._reference_map_source_options()
                self.reference_alignment_source.options = self._reference_alignment_source_options()
                model_options = self._reference_geoid_options()
                self.reference_geoid_model.options = model_options
                self.reference_map_geoid_model.options = model_options
                self.reference_alignment_source.value = "ign"
                self.reference_alignment_resolution.value = 1.0
                self.reference_map_source.value = "ign"
                self.reference_map_resolution.value = 50.0
                self.reference_geoid_model.value = "raf20"
                self.reference_map_geoid_model.value = "raf20"
            finally:
                self._updating_reference_controls = False
            self._rebuild_reference_dem_controls()

        if hasattr(self, "alignment_dem"):
            self.alignment_dem.value = ""
        if hasattr(self, "mapproject_dem"):
            self.mapproject_dem.value = ""
        if hasattr(self, "reference_dem_status"):
            self.reference_dem_status.value = (
                "<span style='color:#666;font-size:12px;'>No reference DEM prepared for the new project.</span>"
            )
        if hasattr(self, "reference_dem_progress"):
            self.reference_dem_progress.value = 0
            self.reference_dem_progress.bar_style = ""
        if hasattr(self, "reference_dem_progress_text"):
            self.reference_dem_progress_text.value = "<span style='color:#666;'>Waiting.</span>"
        if hasattr(self, "reference_dem_qc_outputs"):
            for output in self.reference_dem_qc_outputs.values():
                with output:
                    clear_output(wait=False)
        if hasattr(self, "reference_dem_qc_tabs"):
            self.reference_dem_qc_tabs.layout.display = "none"
            self.reference_dem_qc_tabs.selected_index = 0

        if hasattr(self, "preprocess_summary"):
            self.preprocess_summary.value = ""
            self.preprocess_summary_details.selected_index = None
            self.preprocess_progress.value = 0
            self.preprocess_progress.bar_style = ""
            self.preprocess_progress_text.value = "<span style='color:#666;'>Waiting.</span>"
            self._reset_preprocess_stage_tabs(self._preprocess_stage_order)

        for summary_name, details_name, progress_name in (
            ("point_cloud_summary", "point_cloud_summary_details", "point_cloud_progress"),
            ("final_dsm_summary", "final_dsm_summary_details", "final_dsm_progress"),
        ):
            summary = getattr(self, summary_name, None)
            if summary is not None:
                summary.value = ""
            details = getattr(self, details_name, None)
            if details is not None:
                details.selected_index = None
            progress = getattr(self, progress_name, None)
            if progress is not None:
                progress.value = 0
                progress.bar_style = ""

        if hasattr(self, "point_cloud_results"):
            self.point_cloud_results.children = ()
        if hasattr(self, "final_dsm_results"):
            self.final_dsm_results.children = ()
        if hasattr(self, "coregistration_status"):
            self.coregistration_status.value = ""
        if hasattr(self, "coregistration_output"):
            self.coregistration_output.clear_output()

        self.last_pre_processing = None
        self.last_point_cloud = None
        self.last_final_dsm = None
        self._refresh_preprocess_run_button_state()

    def _restore_processing_state_from_project(self, settings):
        """Restore reusable paths, controls, QC previews, and result panels.

        Loading a project never reruns ASP. Existing products are detected on
        disk and their saved tables/previews are displayed again so reopening
        the notebook behaves as a true resume rather than a blank session.
        """
        settings = settings if isinstance(settings, ProjectSettings) else load_project_config(settings)
        state = {}
        ref = {}
        self._updating_reference_controls = True
        try:
            processing_state_path = settings.project_dir / "processing_state.json"
            if processing_state_path.is_file():
                try:
                    state = json.loads(processing_state_path.read_text(encoding="utf-8"))
                except Exception:
                    state = {}

            pre = state.get("pre_processing", {}) if isinstance(state, dict) else {}
            if pre:
                if pre.get("camera_model") in {"rpc", "pleiades"}:
                    self.camera_model.value = pre["camera_model"]
                if pre.get("target_epsg") not in (None, ""):
                    self.target_epsg.value = int(pre["target_epsg"])
                if pre.get("raw_resolution_m") not in (None, ""):
                    self.map_resolution.value = float(pre["raw_resolution_m"])
                if pre.get("preliminary_pair"):
                    self.preliminary_pair.value = str(pre["preliminary_pair"])

            ref_config_path = settings.project_dir / "reference_dems" / "reference_dem_config.json"
            if ref_config_path.is_file():
                try:
                    ref = json.loads(ref_config_path.read_text(encoding="utf-8"))
                except Exception:
                    ref = {}

            # Restore the reference selectors sufficiently for their QC labels
            # to describe the actual saved reference configuration.
            if ref:
                region = str(ref.get("region") or "").strip()
                region_values = [item[1] if isinstance(item, tuple) else item for item in self.reference_region.options]
                if region in region_values:
                    self.reference_region.value = region

                self.reference_map_source.options = self._reference_map_source_options()
                self.reference_alignment_source.options = self._reference_alignment_source_options()
                self.reference_geoid_model.options = self._reference_geoid_options()
                self.reference_map_geoid_model.options = self._reference_geoid_options()

                def _set_if_available(widget, value):
                    values = [item[1] if isinstance(item, tuple) else item for item in widget.options]
                    if value in values:
                        widget.value = value

                _set_if_available(self.reference_alignment_source, ref.get("alignment_source"))
                _set_if_available(self.reference_map_source, ref.get("map_source"))
                _set_if_available(self.reference_geoid_model, ref.get("alignment_geoid_model") or ref.get("geoid_model"))
                _set_if_available(self.reference_map_geoid_model, ref.get("map_geoid_model") or ref.get("geoid_model"))

                if ref.get("alignment_resolution_m") not in (None, ""):
                    self.reference_alignment_resolution.value = float(ref["alignment_resolution_m"])
                if ref.get("map_resolution_m") not in (None, ""):
                    self.reference_map_resolution.value = float(ref["map_resolution_m"])
                if ref.get("target_epsg") not in (None, ""):
                    self.target_epsg.value = int(ref["target_epsg"])

            alignment = str(ref.get("alignment_dem") or pre.get("alignment_dem") or "").strip()
            map_dem = str(ref.get("mapproject_dem") or pre.get("mapproject_dem") or "").strip()

            if alignment:
                self.alignment_dem.value = alignment
            if map_dem:
                self.mapproject_dem.value = map_dem

            alignment_ok = bool(alignment) and Path(alignment).expanduser().is_file()
            map_ok = bool(map_dem) and Path(map_dem).expanduser().is_file()
            self._reference_dem_ready = alignment_ok and map_ok

            if self._reference_dem_ready:
                self.reference_dem_status.value = (
                    "<div style='margin:6px 0 9px 0;padding:9px 11px;"
                    "border-left:4px solid #2e7d32;background:#f4fbf4;"
                    "color:#444;font-size:12px;line-height:1.5;'>"
                    "<b>✓ Existing reference DEMs detected and reused.</b><br>"
                    f"<b>Alignment reference:</b> <code>{html.escape(alignment)}</code><br>"
                    f"<b>Map-projection reference:</b> <code>{html.escape(map_dem)}</code><br>"
                    "Saved QC/results are restored below; no reference preparation is rerun."
                    "</div>"
                )
            elif alignment or map_dem:
                self.reference_dem_status.value = (
                    "<div style='margin:6px 0 9px 0;padding:9px 11px;"
                    "border-left:4px solid #d28b00;background:#fffaf0;color:#555;font-size:12px;'>"
                    "A previous reference configuration was found, but one or more resolved DEM files "
                    "are missing. Prepare/select the references again before the stages that require them."
                    "</div>"
                )
        finally:
            self._updating_reference_controls = False

        # Re-display the reference DEM QC from the already prepared rasters.
        if self._reference_dem_ready:
            try:
                qc_result = dict(ref)
                qc_result["alignment_dem"] = str(self.alignment_dem.value)
                qc_result["mapproject_dem"] = str(self.mapproject_dem.value)
                qc_result["alignment_n"] = ref.get("alignment_geoid_model_raster")
                qc_result["map_n"] = ref.get("map_geoid_model_raster")
                qc = self._render_reference_dem_qc(settings, qc_result)
                for payload in qc.values():
                    if payload is not None and payload.get("figure") is not None:
                        plt.close(payload["figure"])
                self.reference_dem_progress.value = 100
                self.reference_dem_progress.bar_style = "success"
                self.reference_dem_progress_text.value = (
                    "<span style='color:#2e7d32;'>Existing reference DEM QC restored.</span>"
                )
            except Exception as exc:
                self.reference_dem_progress_text.value = (
                    "<span style='color:#8a5a00;'>Reference DEM files were restored, but the QC preview could not be rebuilt: "
                    + html.escape(type(exc).__name__ + ": " + str(exc)) + "</span>"
                )

        self._restore_existing_processing_result_panels(settings, state)
        self._sync_camera_model_controls()
        self._refresh_preprocess_run_button_state()
        self._refresh_resume_project_status(settings)

    def _restore_existing_processing_result_panels(self, settings, state):
        """Show existing pre/post-processing outputs without rerunning commands."""
        from IPython.display import clear_output

        def _img_html(path, title):
            path = Path(path)
            if not path.is_file():
                return ""
            try:
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                return (
                    "<div style='margin-top:12px;'>"
                    f"<div style='font-size:13px;font-weight:700;margin-bottom:7px;'>{html.escape(title)}</div>"
                    f"<img src='data:image/png;base64,{encoded}' style='max-width:100%;height:auto;"
                    "border:1px solid #cfd6df;background:white;'/></div>"
                )
            except Exception:
                return ""

        pre = state.get("pre_processing", {}) if isinstance(state, dict) else {}
        if pre:
            try:
                base_processing = PreProcessingSettings(
                    alignment_dem=str(pre.get("alignment_dem") or ""),
                    mapproject_dem=str(pre.get("mapproject_dem") or ""),
                )
                processing = _processing_variant_from_saved_state(settings, base_processing) or base_processing
                paths = _preprocessing_paths(settings, processing)
                # Prefer the exact product paths saved by earlier runs, then
                # fall back to legacy filename discovery.  Neither step reruns
                # ASP processing.
                paths = _restore_saved_preprocess_paths(
                    pre, paths, settings.image_names
                )
                paths = _restore_legacy_preprocess_paths(settings, processing, paths)
                images = paths["images"]
                cameras = paths["cameras"]
                views = settings.image_names

                restored = {}
                restored["bundle_adjustment"] = _adjustments_ready(paths["ba_prefix"], images, views, cameras)
                restored["preliminary_stereo"] = Path(paths["prelim_point_cloud"]).is_file()
                restored["preliminary_dem"] = Path(paths["prelim_dem"]).is_file()
                restored["lidar_alignment"] = Path(paths["align_transform"]).is_file()
                restored["camera_transform"] = _adjustments_ready(paths["aligned_ba_prefix"], images, views, cameras)
                restored["map_projection"] = all(Path(paths["mapprojected"][view]).is_file() for view in views)

                # Rebuild the same detailed result payloads used at run time.
                # This is intentionally read-only: existing ASP products/logs are
                # parsed and lightweight QC previews may be rebuilt, but no ASP
                # processing command is executed.
                for stage in self._preprocess_stage_order:
                    if not restored.get(stage):
                        continue

                    payload = None
                    try:
                        if stage == "bundle_adjustment":
                            residual_csv = settings.metadata_dir / "bundle_adjustment_residuals.csv"
                            try:
                                residual_table = _bundle_adjustment_residual_summary(
                                    paths["ba_prefix"], views
                                )
                            except Exception:
                                if not residual_csv.is_file():
                                    raise
                                residual_table = pd.read_csv(residual_csv, index_col=0)
                            adjustment_files = _adjustment_files(
                                paths["ba_prefix"], images, views, cameras
                            )
                            adjustment_table = pd.DataFrame([
                                {
                                    "View": view,
                                    "Image": Path(images[view]).name,
                                    "Camera XML": Path(cameras[view]).name,
                                    "ASP session": f"-t {paths['session_type']}",
                                    "Adjustment / state": Path(adjustment).name,
                                }
                                for view, adjustment in zip(views, adjustment_files)
                            ])
                            payload = {
                                "table": residual_table,
                                "adjustment_table": adjustment_table,
                                "csv": residual_csv,
                                "prefix": paths["ba_prefix"],
                                "camera_model": paths["camera_model"],
                                "session_type": paths["session_type"],
                                "robust_threshold": pre.get("ba_robust_threshold"),
                                "max_iterations": pre.get("ba_max_iterations"),
                                "cost_function": pre.get("ba_cost_function"),
                                "datum": "WGS84",
                                "log": paths["ba_log"],
                            }

                        elif stage == "preliminary_stereo":
                            point_cloud_info = _raster_basic_summary(paths["prelim_point_cloud"])
                            pair = paths["pair"]
                            left, right = pair[0], pair[1]
                            payload = {
                                "pair": pair,
                                "left": images[left],
                                "right": images[right],
                                "prefix": paths["prelim_prefix"],
                                "algorithm": pre.get("preliminary_stereo_algorithm", ""),
                                "cost_mode": pre.get("preliminary_cost_mode", ""),
                                "corr_kernel": pre.get("preliminary_corr_kernel", ""),
                                "subpixel_kernel": pre.get("preliminary_subpixel_kernel", ""),
                                "point_cloud": paths["prelim_point_cloud"],
                                "point_cloud_info": point_cloud_info,
                                "log": paths["prelim_log"],
                            }

                        elif stage == "preliminary_dem":
                            dem_info = _raster_basic_summary(paths["prelim_dem"])
                            png = settings.figure_dir / f"preliminary_{paths['pair']}_alignment_DEM.png"
                            pdf = settings.figure_dir / f"preliminary_{paths['pair']}_alignment_DEM.pdf"
                            if not png.is_file():
                                try:
                                    plot = _plot_preliminary_dem_from_path(
                                        settings, paths["prelim_dem"], paths["pair"]
                                    )
                                    if plot.get("figure") is not None:
                                        plt.close(plot["figure"])
                                except Exception:
                                    plot = {"png": png, "pdf": pdf}
                            else:
                                plot = {"png": png, "pdf": pdf}
                            payload = {
                                "point_cloud": paths["prelim_point_cloud"],
                                "dem": paths["prelim_dem"],
                                "resolution_m": pre.get(
                                    "prelim_dem_resolution_m", dem_info.get("Pixel X", "")
                                ),
                                "nodata": pre.get("prelim_dem_nodata", ""),
                                "target_epsg": pre.get("target_epsg", ""),
                                "dem_info": dem_info,
                                "plot": plot,
                                "log": paths["prelim_dem_log"],
                            }

                        elif stage == "lidar_alignment":
                            if Path(paths["align_log"]).is_file():
                                alignment_results = _parse_pc_align_original_results(
                                    paths["align_log"], paths["align_transform"]
                                )
                            else:
                                alignment_results = {
                                    "important_lines": [],
                                    "matrix": _read_transform_matrix(paths["align_transform"]),
                                    "transform_path": Path(paths["align_transform"]),
                                    "log_path": Path(paths["align_log"]),
                                }
                            payload = {
                                "reference": pre.get("alignment_dem") or self.alignment_dem.value,
                                "source": paths["prelim_dem"],
                                "transform": paths["align_transform"],
                                "max_displacement_m": pre.get("pc_align_max_displacement_m", ""),
                                "iterations": pre.get("pc_align_iterations", ""),
                                "alignment_results": alignment_results,
                                "log": paths["align_log"],
                            }

                        elif stage == "camera_transform":
                            payload = {
                                "transform": paths["align_transform"],
                                "prefix": paths["aligned_ba_prefix"],
                                "table": _camera_adjustment_table(settings, paths),
                                "log": paths["aligned_ba_log"],
                            }

                        elif stage == "map_projection":
                            png = settings.figure_dir / "mapprojected_images_preview.png"
                            pdf = settings.figure_dir / "mapprojected_images_preview.pdf"
                            if not png.is_file():
                                try:
                                    plot = _plot_mapprojected_from_paths(
                                        settings, paths["mapprojected"], int(pre.get("target_epsg", self.target_epsg.value))
                                    )
                                    if plot.get("figure") is not None:
                                        plt.close(plot["figure"])
                                except Exception:
                                    plot = {"png": png, "pdf": pdf}
                            else:
                                plot = {"png": png, "pdf": pdf}
                            payload = {
                                "mapproject_dem": pre.get("mapproject_dem") or self.mapproject_dem.value,
                                "outputs": paths["mapprojected"],
                                "logs": paths["mapproject_logs"],
                                "resolution_m": pre.get("raw_resolution_m", self.map_resolution.value),
                                "target_epsg": pre.get("target_epsg", self.target_epsg.value),
                                "threads": pre.get("mapproject_threads", ""),
                                "table": _mapproject_output_table(settings, paths),
                                "plot": plot,
                            }

                        if payload is not None:
                            self._update_preprocess_stage_result(
                                stage, "completed", payload, restored=True
                            )
                    except Exception as restore_exc:
                        # Fall back to a compact diagnostic rather than hiding an
                        # otherwise valid existing product.
                        index = self._preprocess_stage_order.index(stage)
                        self._preprocess_stage_status[stage] = "completed"
                        self.preprocess_stage_tabs.set_title(
                            index, "✓ " + self._preprocess_stage_labels[stage]
                        )
                        self._preprocess_stage_html[stage].value = (
                            "<div style='padding:10px;border-left:4px solid #d28b00;background:#fffaf0;'>"
                            "<b>✓ Existing result detected.</b><br>"
                            "The detailed display could not be rebuilt from this older project, "
                            "but the processing product remains available on disk.<br>"
                            f"<span style='color:#666;font-size:12px;'>{html.escape(type(restore_exc).__name__ + ': ' + str(restore_exc))}</span>"
                            "</div>"
                        )

                self.preprocess_results.children = (self.preprocess_stage_tabs,)
            except Exception:
                # Resume display is diagnostic only; never prevent loading a project.
                pass

        # Restore point-cloud product table.
        pc_csv = settings.metadata_dir / "point_cloud_products.csv"
        if pc_csv.is_file():
            try:
                pc_df = pd.read_csv(pc_csv)
                self.point_cloud_results.children = (self.widgets.HTML(self._dataframe_html(pc_df)),)
                self.point_cloud_summary.value = (
                    "<div style='margin:10px 0;padding:10px;border-left:4px solid #2e7d32;background:#f4fbf4;'>"
                    "<b>✓ Existing point-cloud products restored.</b><br>"
                    f"<b>Product table:</b> <code>{html.escape(str(pc_csv))}</code><br>"
                    "<span style='color:#666;font-size:12px;'>No stereo reconstruction was rerun.</span></div>"
                )
                self.point_cloud_summary_details.selected_index = None
                self.point_cloud_progress.value = 100
                self.point_cloud_progress.bar_style = "success"
                self.point_cloud_progress_text.value = "<span style='color:#2e7d32;'>Existing products restored.</span>"
            except Exception:
                pass

        # Restore final DSM product table and saved QC previews.
        final_csv = settings.metadata_dir / "final_dsm_products.csv"
        if final_csv.is_file():
            try:
                final_df = pd.read_csv(final_csv)
                children = [self.widgets.HTML(self._dataframe_html(final_df))]
                titles = ["Generated DSMs"]
                for _, row in final_df.iterrows():
                    tag = str(row.get("Product", "DSM"))
                    dsm = str(row.get("DSM", ""))
                    err = str(row.get("Intersection error", ""))
                    previews = sorted(settings.figure_dir.glob(f"{tag}_{settings.project_name}_*_final_DSM.png"))
                    preview = previews[0] if previews else None
                    body = (
                        "<div style='padding:10px;'>"
                        f"<b>Final DSM:</b> <code>{html.escape(dsm)}</code><br>"
                        f"<b>Intersection error:</b> <code>{html.escape(err if err and err.lower() != 'nan' else 'Not generated')}</code>"
                    )
                    if preview is not None:
                        body += _img_html(preview, "Final DSM + intersection error")
                    body += "</div>"
                    children.append(self.widgets.HTML(body))
                    titles.append(tag)
                tabs = self.widgets.Tab(children=children)
                for i, title in enumerate(titles):
                    tabs.set_title(i, title)
                self.final_dsm_results.children = (tabs,)
                self.final_dsm_summary.value = (
                    "<div style='margin:10px 0;padding:10px;border-left:4px solid #2e7d32;background:#f4fbf4;'>"
                    "<b>✓ Existing final DSM products restored.</b><br>"
                    f"<b>Product table:</b> <code>{html.escape(str(final_csv))}</code><br>"
                    "<span style='color:#666;font-size:12px;'>No DSM generation was rerun.</span></div>"
                )
                self.final_dsm_summary_details.selected_index = None
                self.final_dsm_progress.value = 100
                self.final_dsm_progress.bar_style = "success"
                self.final_dsm_progress_text.value = "<span style='color:#2e7d32;'>Existing DSMs restored.</span>"
            except Exception:
                pass

    # --------------------------------------------------------
    # REFERENCE DEM CONTROLS
    # --------------------------------------------------------
    def _reference_map_source_options(self):
        options = [
            ("Copernicus DEM GLO-30", "copernicus"),
            ("SRTM1 30 m", "srtm"),
        ]

        if self.reference_region.value == "france":
            options.insert(
                0,
                ("IGN LiDAR HD", "ign"),
            )

        options.append(
            ("Existing DEM", "existing")
        )
        return options

    def _reference_alignment_source_options(self):
        options = [
            (
                "Copernicus DEM GLO-30 — ~30 m global reference",
                "copernicus",
            ),
            (
                "SRTM1 — ~30 m global reference",
                "srtm",
            ),
            (
                "Existing DEM — user supplied",
                "existing",
            ),
        ]

        if self.reference_region.value == "france":
            options.insert(
                0,
                (
                    "IGN LiDAR HD — 1 m preferred reference",
                    "ign",
                ),
            )

        return options

    def _reference_geoid_options(self):
        options = []

        if self.reference_region.value == "france":
            options.append(
                ("RAF20 — mainland France", "raf20")
            )

        options.extend(
            [
                ("EGM96 — global", "egm96"),
                ("EGM2008 — global", "egm2008"),
                ("Custom N raster", "custom"),
            ]
        )

        return options

    def _reference_default_geoid_model(self, source=None):
        """Return the editable default vertical model for one reference role."""
        # User-requested rule: inside mainland France RAF20 is the default
        # regardless of whether the selected source is IGN, Copernicus, SRTM,
        # or an existing DEM. Outside France, the default follows the selected
        # elevation source and remains user-editable.
        if self.reference_region.value == "france":
            return "raf20"

        source = source or self.reference_alignment_source.value
        if source == "srtm":
            return "egm96"
        if source == "copernicus":
            return "egm2008"
        return "egm96"

    def _configure_reference_dem_controls(self):
        if getattr(self, "_updating_reference_controls", False):
            return

        self._updating_reference_controls = True
        try:
            current_map = self.reference_map_source.value
            map_options = self._reference_map_source_options()
            self.reference_map_source.options = map_options
            valid_map = {value for _, value in map_options}

            current_alignment = self.reference_alignment_source.value
            alignment_options = self._reference_alignment_source_options()
            self.reference_alignment_source.options = alignment_options
            valid_alignment = {value for _, value in alignment_options}

            desired_alignment = (
                current_alignment if current_alignment in valid_alignment
                else ("ign" if self.reference_region.value == "france" else "copernicus")
            )
            self.reference_alignment_source.value = desired_alignment

            desired_map = (
                current_map if current_map in valid_map
                else desired_alignment if desired_alignment in valid_map
                else ("ign" if self.reference_region.value == "france" else "copernicus")
            )
            self.reference_map_source.value = desired_map

            model_options = self._reference_geoid_options()
            self.reference_geoid_model.options = model_options
            self.reference_map_geoid_model.options = model_options
            valid_models = {value for _, value in model_options}

            alignment_default = self._reference_default_geoid_model(desired_alignment)
            map_default = self._reference_default_geoid_model(desired_map)
            self.reference_geoid_model.value = (
                alignment_default if alignment_default in valid_models else model_options[0][1]
            )
            self.reference_map_geoid_model.value = (
                map_default if map_default in valid_models else model_options[0][1]
            )

            # Source-specific defaults only.  Both resolution fields remain
            # editable after initialization.
            if desired_alignment == "ign":
                self.reference_alignment_resolution.value = 1.0
            elif desired_alignment in {"copernicus", "srtm"}:
                self.reference_alignment_resolution.value = 30.0

            self._apply_reference_map_resolution_default()
        finally:
            self._updating_reference_controls = False

        self._rebuild_reference_dem_controls()

    def _apply_reference_map_resolution_default(self):
        source = self.reference_map_source.value

        if source == "ign":
            # Tested workflow:
            # 1 m LiDAR for pc_align, generalized 50 m LiDAR for mapproject.
            self.reference_map_resolution.value = 50.0

        elif source in {
            "copernicus",
            "srtm",
        }:
            self.reference_map_resolution.value = 30.0

        elif source == "existing":
            # Keep the tested generalized-reference value as a neutral default.
            self.reference_map_resolution.value = 50.0

    def _on_reference_region_change(self, change=None):
        if getattr(self, "_updating_reference_controls", False):
            return

        self._updating_reference_controls = True
        try:
            self.reference_map_source.options = self._reference_map_source_options()
            self.reference_alignment_source.options = self._reference_alignment_source_options()
            model_options = self._reference_geoid_options()
            self.reference_geoid_model.options = model_options
            self.reference_map_geoid_model.options = model_options

            if self.reference_region.value == "france":
                # Preserve the tested French defaults, but keep every selector editable.
                self.reference_alignment_source.value = "ign"
                self.reference_alignment_resolution.value = 1.0
                self.reference_map_source.value = "ign"
                self.reference_map_resolution.value = 50.0
                self.reference_geoid_model.value = "raf20"
                self.reference_map_geoid_model.value = "raf20"
            else:
                self.reference_alignment_source.value = "copernicus"
                self.reference_alignment_resolution.value = 30.0
                self.reference_map_source.value = "copernicus"
                self.reference_map_resolution.value = 30.0
                self.reference_geoid_model.value = "egm2008"
                self.reference_map_geoid_model.value = "egm2008"
        finally:
            self._updating_reference_controls = False

        self._rebuild_reference_dem_controls()

    def _on_reference_map_source_change(self, change=None):
        if getattr(self, "_updating_reference_controls", False):
            return

        self._updating_reference_controls = True
        try:
            self._apply_reference_map_resolution_default()
            model_options = self._reference_geoid_options()
            self.reference_map_geoid_model.options = model_options
            default_model = self._reference_default_geoid_model(self.reference_map_source.value)
            valid_models = {value for _, value in model_options}
            if default_model in valid_models:
                self.reference_map_geoid_model.value = default_model
        finally:
            self._updating_reference_controls = False

        self._rebuild_reference_dem_controls()

    def _sync_prelim_dem_resolution_from_alignment(self, change=None):
        """Use the alignment DEM resolution as the preliminary DSM default.

        The preliminary DSM field remains fully editable. If the user changes
        it manually after this synchronization, that manual value is kept until
        the alignment DEM resolution itself is changed again.
        """
        try:
            value = float(self.reference_alignment_resolution.value)
        except (TypeError, ValueError):
            return

        if not np.isfinite(value) or value <= 0:
            return

        self.prelim_dem_resolution.value = value

    def _on_reference_alignment_source_change(self, change=None):
        if getattr(self, "_updating_reference_controls", False):
            return

        self._updating_reference_controls = True
        try:
            source = self.reference_alignment_source.value

            # Suggested alignment working resolution; never lock the field.
            if source == "ign":
                self.reference_alignment_resolution.value = 1.0
            elif source in {"copernicus", "srtm"}:
                self.reference_alignment_resolution.value = 30.0

            # Mapprojection initially follows the selected alignment source,
            # but remains independently editable immediately afterwards.
            map_values = {value for _, value in self._reference_map_source_options()}
            if source in map_values:
                self.reference_map_source.value = source
                self._apply_reference_map_resolution_default()

            model_options = self._reference_geoid_options()
            self.reference_geoid_model.options = model_options
            self.reference_map_geoid_model.options = model_options
            valid_models = {value for _, value in model_options}

            alignment_default = self._reference_default_geoid_model(source)
            if alignment_default in valid_models:
                self.reference_geoid_model.value = alignment_default

            # Since the map source was just defaulted to match, initialize its
            # vertical model too. The user may then change it independently.
            map_default = self._reference_default_geoid_model(self.reference_map_source.value)
            if map_default in valid_models:
                self.reference_map_geoid_model.value = map_default
        finally:
            self._updating_reference_controls = False

        self._rebuild_reference_dem_controls()

    def _reference_vertical_models_are_split(self):
        """Use one vertical selector by default; split when sources differ or user asks."""
        return (
            self.reference_alignment_source.value != self.reference_map_source.value
            or bool(self.reference_split_vertical_models.value)
        )

    def _on_reference_alignment_geoid_change(self, change=None):
        if getattr(self, "_updating_reference_controls", False):
            return
        # In shared mode, one visible selector controls both roles.
        if not self._reference_vertical_models_are_split():
            self._updating_reference_controls = True
            try:
                self.reference_map_geoid_model.value = self.reference_geoid_model.value
            finally:
                self._updating_reference_controls = False
        self._rebuild_reference_dem_controls()

    def _on_reference_split_vertical_change(self, change=None):
        if getattr(self, "_updating_reference_controls", False):
            return
        # If the user returns to shared mode, propagate the alignment/shared model.
        if not self._reference_vertical_models_are_split():
            self._updating_reference_controls = True
            try:
                self.reference_map_geoid_model.value = self.reference_geoid_model.value
            finally:
                self._updating_reference_controls = False
        self._rebuild_reference_dem_controls()

    def _rebuild_reference_dem_controls(self, change=None):
        if getattr(self, "_updating_reference_controls", False):
            return

        # All source and resolution controls stay editable.  Copernicus/SRTM
        # are natively ~30 m; finer requested grids are interpolation only.
        self.reference_alignment_resolution.disabled = False
        self.reference_map_resolution.disabled = False

        sources_differ = self.reference_alignment_source.value != self.reference_map_source.value
        if sources_differ:
            # Two different DEM sources can legitimately have different native vertical references.
            # Force the UI into two-role mode while preserving the user's manual preference for later.
            self.reference_split_vertical_models.disabled = True
            split_vertical = True
        else:
            self.reference_split_vertical_models.disabled = False
            split_vertical = bool(self.reference_split_vertical_models.value)
            if not split_vertical and self.reference_map_geoid_model.value != self.reference_geoid_model.value:
                self._updating_reference_controls = True
                try:
                    self.reference_map_geoid_model.value = self.reference_geoid_model.value
                finally:
                    self._updating_reference_controls = False

        children = [
            self.widgets.HTML(
                value=(
                    "<div style='color:#555;margin-bottom:8px;line-height:1.5;'>"
                    "<b>ASP reference-height requirement:</b> automatically "
                    "downloaded reference DEMs are converted to "
                    "<b>ellipsoidal heights</b> before ASP.<br>"
                    "The prepared alignment and map-projection DEMs are also "
                    "reprojected to the <b>Target CRS (EPSG)</b> selected under "
                    "Advanced pre-processing."
                    "</div>"
                )
            ),
            self.widgets.HTML("<b>Reference DEM location and defaults</b>"),
            self._row(
                self.reference_region,
                "France starts from the tested IGN LiDAR + RAF20 configuration. "
                "Other/global starts from Copernicus GLO-30 for both alignment "
                "and map projection; a higher-resolution alignment DEM can be "
                "selected whenever one is available.",
            ),
            self._row(
                self.reference_aoi,
                "Normally leave this blank: the software reuses the AOI vector "
                "already entered under Prepare data. Enter a separate AOI only "
                "when no Prepare-data AOI exists or when a different reference "
                "DEM download extent is required.",
            ),
            self.widgets.HTML(
                value=(
                    "<div style='margin:2px 0 7px 205px;color:#666;"
                    "max-width:760px;font-size:12px;line-height:1.45;'>"
                    "<b>AOI behavior:</b> blank = reuse <b>Prepare data AOI</b>. "
                    "If both are blank and a DEM download is requested, "
                    "Pre-processing will ask for an AOI."
                    "</div>"
                )
            ),
            self.widgets.HTML(
                "<div style='margin:18px 0 8px 0;padding:8px 10px;"
                "border-left:4px solid #4b6f8a;background:#f4f7fa;"
                "font-size:13px;font-weight:700;'>A. Alignment reference for pc_align</div>"
            ),
            self._row(
                self.reference_alignment_source,
                "Use the best external elevation reference available. "
                "IGN LiDAR HD / another high-resolution DEM is preferred; "
                "Copernicus GLO-30 and SRTM1 are supported global fallbacks.",
            ),
            self._row(
                self.reference_alignment_resolution,
                "Working resolution of the pc_align reference. "
                "IGN LiDAR default: 1 m; Copernicus/SRTM: ~30 m.",
            ),
        ]

        if self.reference_alignment_source.value == "existing":
            children.extend(
                [
                    self._row(
                        self.reference_existing_alignment,
                        "User-supplied DEM used by pc_align. A high-resolution "
                        "DSM/DTM is preferred when available.",
                    ),
                    self.reference_existing_alignment_convert,
                    self.widgets.HTML(
                        value=(
                            "<div style='margin:4px 0 5px 205px;color:#8a5a00;"
                            "max-width:760px;font-size:12px;line-height:1.45;'>"
                            "<b>Note:</b> if conversion is not selected, the "
                            "existing alignment DEM is assumed to already "
                            "contain ellipsoidal heights."
                            "</div>"
                        )
                    ),
                ]
            )
        elif self.reference_alignment_source.value in {"copernicus", "srtm"}:
            source_name = (
                "Copernicus DEM GLO-30"
                if self.reference_alignment_source.value == "copernicus"
                else "SRTM1"
            )
            children.append(
                self.widgets.HTML(
                    value=(
                        "<div style='margin:5px 0 7px 205px;padding:9px 11px;"
                        "border-left:4px solid #d28b00;background:#fff8e8;"
                        "color:#5f4a18;max-width:760px;font-size:12px;"
                        "line-height:1.5;'>"
                        f"<b>Coarse global alignment reference — {source_name} (~30 m).</b><br>"
                        "This keeps the workflow usable where airborne LiDAR or "
                        "another high-resolution DEM is unavailable. For steep "
                        "relief, narrow valleys, and accuracy-sensitive work, a "
                        "higher-resolution reference is recommended when available.<br>"
                        "<b>Resolution:</b> the working-resolution field remains editable. "
                        "Choosing a grid finer than the source native resolution does not "
                        "create new topographic detail."
                        "</div>"
                    )
                )
            )
        else:
            children.append(
                self.widgets.HTML(
                    value=(
                        "<div style='margin:4px 0 5px 205px;color:#555;"
                        "max-width:760px;font-size:12px;line-height:1.45;'>"
                        "IGN LiDAR HD is downloaded automatically and prepared "
                        "as the preferred high-resolution ellipsoidal alignment reference."
                        "</div>"
                    )
                )
            )

        children.append(
            self.widgets.HTML(
                "<div style='margin:18px 0 8px 0;padding:8px 10px;"
                "border-left:4px solid #4b6f8a;background:#f4f7fa;"
                "font-size:13px;font-weight:700;'>B. Map-projection reference</div>"
            )
        )

        children.extend(
            [
                self._row(
                    self.reference_map_source,
                    "Mapprojection defaults to the alignment source when that source is "
                    "changed, but remains independent. You can select IGN LiDAR, "
                    "Copernicus, SRTM, or an existing DEM separately.",
                ),
                self._row(
                    self.reference_map_resolution,
                    "Independent map-projection working resolution. LiDAR default: "
                    "50 m; Copernicus/SRTM default: 30 m. Any positive value may be "
                    "entered. If it differs from the alignment resolution, a separate "
                    "prepared DEM is generated.",
                ),
            ]
        )

        if self.reference_map_source.value == "existing":
            children.extend(
                [
                    self._row(
                        self.reference_existing_map,
                        "Existing map-projection DEM. It will be reprojected/"
                        "resampled to the selected Target CRS and resolution.",
                    ),
                    self.reference_existing_map_convert,
                    self.widgets.HTML(
                        value=(
                            "<div style='margin:4px 0 5px 205px;color:#8a5a00;"
                            "max-width:760px;font-size:12px;line-height:1.45;'>"
                            "<b>Note:</b> if conversion is not selected, the "
                            "existing map-projection DEM is assumed to already "
                            "contain the vertical heights intended for ASP."
                            "</div>"
                        )
                    ),
                ]
            )

        conversion_needed = (
            self.reference_map_source.value
            in {
                "ign",
                "copernicus",
                "srtm",
            }
            or self.reference_alignment_source.value
            in {"ign", "copernicus", "srtm"}
            or (
                self.reference_map_source.value == "existing"
                and self.reference_existing_map_convert.value
            )
            or (
                self.reference_alignment_source.value == "existing"
                and self.reference_existing_alignment_convert.value
            )
        )

        if conversion_needed:
            children.append(
                self.widgets.HTML(
                    "<div style='margin:18px 0 8px 0;padding:8px 10px;"
                    "border-left:4px solid #4b6f8a;background:#f4f7fa;"
                    "font-size:13px;font-weight:700;'>C. Vertical reference conversion</div>"
                )
            )
            children.append(
                self.widgets.HTML(
                    "<div style='margin:2px 0 7px 205px;color:#666;max-width:780px;"
                    "font-size:12px;line-height:1.45;'>"
                    "One vertical-reference selector is used by default. If Alignment and "
                    "Map-projection use different DEM sources, both role-specific selectors "
                    "appear automatically. With the same source, enable the checkbox below "
                    "only if the two roles truly require different vertical models."
                    "</div>"
                )
            )
            if sources_differ:
                children.append(
                    self.widgets.HTML(
                        "<div style='margin:4px 0 7px 205px;padding:7px 9px;"
                        "border-left:3px solid #7b5fa6;background:#faf8ff;"
                        "color:#555;max-width:780px;font-size:12px;line-height:1.45;'>"
                        "<b>Separate vertical references activated automatically:</b> "
                        "Alignment and Map-projection use different DEM sources."
                        "</div>"
                    )
                )
            else:
                children.append(self.reference_split_vertical_models)

            if split_vertical:
                self.reference_geoid_model.description = "Alignment vertical ref:"
                self.reference_map_geoid_model.description = "Map vertical ref:"
                children.extend([
                    self._row(
                        self.reference_geoid_model,
                        "Vertical model for the pc_align reference. France defaults to RAF20; "
                        "outside France the default follows the selected alignment source.",
                    ),
                    self._row(
                        self.reference_map_geoid_model,
                        "Vertical model for the map-projection reference. France defaults to RAF20; "
                        "outside France the default follows the selected map source.",
                    ),
                ])
            else:
                self.reference_geoid_model.description = "Vertical reference:"
                children.append(
                    self._row(
                        self.reference_geoid_model,
                        "Shared vertical model for both pc_align and mapprojection. France defaults "
                        "to RAF20 for every source; outside France the default follows the selected "
                        "reference source. The same value is applied internally to both roles.",
                    )
                )

            if self.reference_geoid_model.value == "custom":
                children.append(
                    self._row(
                        self.reference_custom_n,
                        "Custom geoid/quasi-geoid separation raster for the alignment/shared "
                        "vertical reference, containing N = h − H in metres.",
                    )
                )

            if split_vertical and self.reference_map_geoid_model.value == "custom":
                children.append(
                    self._row(
                        self.reference_map_custom_n,
                        "Map-projection custom geoid/quasi-geoid separation raster containing "
                        "N = h − H in metres.",
                    )
                )

            if self.reference_region.value == "france":
                children.append(
                    self.widgets.HTML(
                        value=(
                            "<div style='margin:4px 0 5px 205px;"
                            "padding:8px 10px;border-left:3px solid #336699;"
                            "background:#f7f9fc;color:#555;max-width:760px;"
                            "font-size:12px;line-height:1.45;'>"
                            "<b>France default:</b> RAF20 is the default vertical reference "
                            "for every DEM source. If two different reference sources are selected, "
                            "both role selectors are shown but each still starts from RAF20."
                            "</div>"
                        )
                    )
                )

        children.extend(
            [
                self.widgets.HTML(
                    "<div style='margin:18px 0 8px 0;padding:8px 10px;"
                    "border-left:4px solid #4b6f8a;background:#f4f7fa;"
                    "font-size:13px;font-weight:700;'>D. Download settings</div>"
                ),
                self._row(
                    self.reference_global_buffer,
                    "Buffer around the AOI for Copernicus/SRTM tile selection.",
                ),
            ]
        )

        if (
            self.reference_region.value == "france"
            and (
                self.reference_map_source.value == "ign"
                or self.reference_alignment_source.value == "ign"
            )
        ):
            children.extend(
                [
                    self._row(
                        self.reference_ign_buffer,
                        "Safety buffer around the AOI for IGN LiDAR HD tiles.",
                    ),
                    self._row(
                        self.reference_ign_workers,
                        "Concurrent IGN LiDAR HD tile downloads.",
                    ),
                ]
            )

        self.reference_dem_box.children = tuple(
            children
        )

    def _resolved_reference_aoi(self):
        value = self.reference_aoi.value.strip()

        if value:
            return value

        return self.aoi_vector.value.strip()

    def _prepare_reference_dem_inputs(self, settings):
        from pleiades_reference_dem import (
            IntegratedReferenceDEMSettings,
            prepare_integrated_reference_dems,
        )

        ref_settings = IntegratedReferenceDEMSettings(
            project_dir=settings.project_dir,
            target_epsg=int(
                self.target_epsg.value
            ),
            region=self.reference_region.value,
            aoi_path=self._resolved_reference_aoi(),
            map_source=self.reference_map_source.value,
            map_resolution_m=float(
                self.reference_map_resolution.value
            ),
            map_existing_path=(
                self.reference_existing_map.value.strip()
            ),
            map_existing_convert_to_ellipsoid=bool(
                self.reference_existing_map_convert.value
            ),
            alignment_source=self.reference_alignment_source.value,
            alignment_resolution_m=float(
                self.reference_alignment_resolution.value
            ),
            alignment_existing_path=(
                self.reference_existing_alignment.value.strip()
            ),
            alignment_existing_convert_to_ellipsoid=bool(
                self.reference_existing_alignment_convert.value
            ),
            geoid_model=self.reference_geoid_model.value,
            custom_n_raster=(
                self.reference_custom_n.value.strip()
            ),
            alignment_geoid_model=self.reference_geoid_model.value,
            map_geoid_model=self.reference_map_geoid_model.value,
            alignment_custom_n_raster=self.reference_custom_n.value.strip(),
            map_custom_n_raster=self.reference_map_custom_n.value.strip(),
            global_buffer_deg=float(
                self.reference_global_buffer.value
            ),
            ign_buffer_m=float(
                self.reference_ign_buffer.value
            ),
            ign_workers=int(
                self.reference_ign_workers.value
            ),
        )

        result = prepare_integrated_reference_dems(
            ref_settings,
            progress_callback=self._set_preprocess_progress,
        )

        # Feed the prepared paths into the original, unchanged ASP pipeline.
        self.alignment_dem.value = str(
            result["alignment_dem"]
        )
        self.mapproject_dem.value = str(
            result["mapproject_dem"]
        )

        model_text = html.escape(
            self.reference_geoid_model.label
            if hasattr(
                self.reference_geoid_model,
                "label",
            )
            else str(
                self.reference_geoid_model.value
            )
        )

        shared_note = (
            "<br><b>Reuse:</b> the same prepared global DEM is used for "
            "<code>pc_align</code> and <code>mapproject</code>; no duplicate "
            "reference DEM was generated."
            if result.get("shared_alignment_map_reference")
            else ""
        )

        self.reference_dem_status.value = (
            "<div style='margin:6px 0 9px 0;padding:9px 11px;"
            "border-left:4px solid #2e7d32;background:#f4fbf4;"
            "color:#444;font-size:12px;line-height:1.5;'>"
            "<b>✓ Reference DEMs prepared.</b><br>"
            "<b>Alignment reference:</b> "
            f"<code>{html.escape(str(result['alignment_dem']))}</code><br>"
            "<b>Map-projection reference:</b> "
            f"<code>{html.escape(str(result['mapproject_dem']))}</code>"
            f"{shared_note}<br>"
            "<b>Configuration:</b> "
            f"<code>{html.escape(str(result['config_path']))}</code>"
            "</div>"
        )

        return result

    # --------------------------------------------------------
    # SETTINGS BUILDERS
    # --------------------------------------------------------
    def _build_pre_processing_settings(self, required_stages=None):
        selected_stages = _normalized_preprocess_stages(required_stages)
        selected_set = set(selected_stages)

        alignment_dem = self.alignment_dem.value.strip()
        mapproject_dem = self.mapproject_dem.value.strip()

        if "lidar_alignment" in selected_set and not alignment_dem:
            raise ValueError(
                "This preprocessing selection requires an alignment reference "
                "DEM. Prepare/select the reference DEM first."
            )

        if "map_projection" in selected_set and not mapproject_dem:
            raise ValueError(
                "This preprocessing selection requires the generalized "
                "map-projection DEM. Prepare/select the map-projection DEM first."
            )

        return PreProcessingSettings(
            alignment_dem=alignment_dem,
            mapproject_dem=mapproject_dem,
            camera_model=self.camera_model.value,
            target_epsg=int(self.target_epsg.value),
            raw_resolution_m=float(self.map_resolution.value),
            preliminary_pair=self.preliminary_pair.value,
            ba_cost_function=_clean_optional_string(self.ba_cost_function.value),
            ba_robust_threshold=_parse_optional_float(self.ba_robust_threshold.value, "BA robust threshold"),
            ba_max_iterations=_parse_optional_int(self.ba_max_iterations.value, "BA max iterations"),
            prelim_stereo_algorithm=self._resolved_prelim_algorithm(),
            prelim_xcorr_threshold=float(self.prelim_xcorr_threshold.value),
            prelim_cost_mode=int(self.prelim_cost_mode.value),
            prelim_corr_kernel=int(self._resolved_prelim_kernel_pair()[0]),
            prelim_subpixel_kernel=int(self._resolved_prelim_kernel_pair()[1]),
            prelim_subpixel_mode=int(self.prelim_subpixel_mode.value),
            corr_memory_limit_mb=int(self.prelim_corr_memory.value),
            corr_tile_size=int(self.prelim_corr_tile_size.value),
            prelim_dem_resolution_m=float(self.prelim_dem_resolution.value),
            prelim_dem_nodata=float(self.prelim_dem_nodata.value),
            pc_align_max_displacement_m=float(
                self.pc_align_max_displacement.value
            ),
            pc_align_iterations=int(self.pc_align_iterations.value),
            aligned_ba_threads=18,
            mapproject_threads=int(self.mapproject_threads.value),
        )

    def _build_final_settings(self):
        kernel_selection = tuple(
            self.kernel_selector.value
        )

        custom_kernel_selected = (
            "CUSTOM_KERNEL" in kernel_selection
        )

        selected_pairs = [
            tuple(value)
            for value in kernel_selection
            if value != "CUSTOM_KERNEL"
        ]

        if custom_kernel_selected:
            custom_pairs = _parse_custom_kernel_pairs(
                self.custom_kernel_pairs.value
            )

            if not custom_pairs:
                raise ValueError(
                    "You selected 'Other / custom CK:SK'. "
                    "Enter at least one pair in the 'Additional CK:SK' field, "
                    "for example 11:23."
                )

            selected_pairs.extend(
                custom_pairs
            )

        unique_pairs = []
        for pair in selected_pairs:
            if pair not in unique_pairs:
                unique_pairs.append(pair)

        algorithm_choice = self.final_algorithm.value

        if algorithm_choice == "CUSTOM":
            algorithm_tag = (
                self.custom_algorithm_name.value.strip()
            )

            if not algorithm_tag:
                raise ValueError(
                    "Select an algorithm preset or enter a custom "
                    "ASP stereo algorithm."
                )
        else:
            algorithm_tag = algorithm_choice

        return FinalProcessingSettings(
            stereo_mode=self.final_mode.value,
            single_pairs=tuple(
                self.single_pair_selector.value
            ),
            dual_configurations=tuple(
                self.dual_selector.value
            ),
            algorithm_tag=algorithm_tag,
            custom_cost_mode=int(
                self.custom_cost_mode.value
            ),
            kernel_pairs=tuple(
                unique_pairs
            ),
            xcorr_threshold=float(
                self.final_xcorr_threshold.value
            ),
            corr_memory_limit_mb=int(
                self.final_corr_memory.value
            ),
            corr_tile_size=int(
                self.final_corr_tile_size.value
            ),
            subpixel_mode=int(
                self.final_subpixel_mode.value
            ),
            pc_merge_threads=int(
                self.pc_merge_threads.value
            ),
            final_dsm_resolution_m=float(
                self.final_dsm_resolution.value
            ),
            max_valid_triangulation_error_m=float(
                self.max_triangulation_error.value
            ),
            final_dsm_threads=int(
                self.final_dsm_threads.value
            ),
            create_error_image=bool(
                self.create_error_image.value
            ),
        )

    def _prelim_kernel_presets(self):
        choice = self.prelim_algorithm.value

        if choice == "BM":
            return (
                (5, 9),
                (7, 15),
                (9, 21),
                (15, 25),
                (25, 35),
                (35, 45),
            )

        if choice in {"SGM", "MGM"}:
            return (
                (5, 9),
                (7, 15),
                (9, 21),
            )

        # Custom algorithm: provide the common starting presets but allow
        # the user to select Other/custom CK:SK.
        return (
            (5, 9),
            (7, 15),
            (9, 21),
        )

    def _resolved_prelim_algorithm(self):
        choice = self.prelim_algorithm.value

        if choice in FINAL_ALGORITHMS:
            return FINAL_ALGORITHMS[choice]["asp_algorithm"]

        value = self.prelim_custom_algorithm.value.strip()
        if not value:
            raise ValueError(
                "Select a preliminary algorithm preset or enter the custom "
                "ASP --stereo-algorithm value."
            )
        return value

    def _resolved_prelim_kernel_pair(self):
        value = self.prelim_kernel_selector.value

        if value == "CUSTOM_KERNEL":
            custom = self.prelim_custom_kernel.value.strip()
            pairs = _parse_custom_kernel_pairs(custom)

            if len(pairs) != 1:
                raise ValueError(
                    "Enter exactly one custom preliminary CK:SK pair, "
                    "for example 11:23."
                )
            return tuple(pairs[0])

        if not (
            isinstance(value, tuple)
            and len(value) == 2
        ):
            raise ValueError(
                "Select a preliminary CK:SK pair."
            )

        return tuple(value)

    def _apply_prelim_algorithm_selection(self):
        choice = self.prelim_algorithm.value

        if choice in FINAL_ALGORITHMS:
            preset = FINAL_ALGORITHMS[choice]
            self.prelim_cost_mode.value = int(
                preset["cost_mode"]
            )
            self.prelim_cost_mode.disabled = True
        else:
            self.prelim_cost_mode.disabled = False

        pairs = self._prelim_kernel_presets()
        previous = self.prelim_kernel_selector.value

        options = [
            (f"{ck} : {sk}", (ck, sk))
            for ck, sk in pairs
        ]
        options.append(
            (
                "Other / custom CK:SK → enter below",
                "CUSTOM_KERNEL",
            )
        )
        self.prelim_kernel_selector.options = options

        # Preserve a compatible user choice where possible.
        valid_values = set(pairs) | {"CUSTOM_KERNEL"}
        if previous in valid_values:
            self.prelim_kernel_selector.value = previous
        elif choice == "BM":
            # Exact tested preliminary BM default.
            self.prelim_kernel_selector.value = (35, 45)
        else:
            self.prelim_kernel_selector.value = (9, 21)

        self._rebuild_preprocess_advanced_controls()

    def _rebuild_preprocess_advanced_controls(self):
        def section(title, text=""):
            subtitle = (
                f"<div style='color:#666;font-size:12px;margin-top:3px;'>{text}</div>"
                if text else ""
            )
            return self.widgets.HTML(
                "<div style='margin:16px 0 8px 0;padding:10px 12px;"
                "border-left:5px solid #4b6f8a;background:#f2f6fa;"
                "border-top:1px solid #d9e2ea;border-bottom:1px solid #d9e2ea;'>"
                f"<div style='font-size:15px;font-weight:700;color:#29465b;'>"
                f"{html.escape(title)}</div>{subtitle}</div>"
            )

        all_mode = (
            not hasattr(self, "preprocess_run_mode")
            or self.preprocess_run_mode.value == "all"
        )
        selected_stage = (
            None if all_mode else str(self.preprocess_single_stage.value)
        )
        children = []

        def wanted(stage):
            return all_mode or selected_stage == stage

        # Show shared controls only when they are actually needed by the
        # selected single stage. In Run all mode the familiar full layout is
        # retained.
        needs_camera = all_mode or selected_stage in {
            "bundle_adjustment", "preliminary_stereo", "camera_transform", "map_projection"
        }
        needs_crs = all_mode or selected_stage in {"preliminary_dem", "map_projection"}

        if needs_camera or needs_crs:
            children.append(
                section(
                    "Shared camera / CRS",
                    "Only controls required by the selected processing mode are shown.",
                )
            )
            if needs_camera:
                children.extend([
                    self._row(
                        self.camera_model,
                        "RPC uses RPC_A.XML/RPC_B.XML/RPC_C.XML with -t rpc. Pléiades exact uses "
                        "DIM_A.XML/DIM_B.XML/DIM_C.XML with -t pleiades.",
                    ),
                    self.camera_model_note,
                ])
            if needs_crs:
                children.extend([self.target_epsg_row, self.crs_information_note])

        if wanted("bundle_adjustment"):
            children.extend([
                section(
                    "1. Bundle adjustment",
                    "Parameters passed to the first bundle_adjust command.",
                ),
                self._row(
                    self.ba_cost_function,
                    "bundle_adjust --cost-function. Tested workflow value: Cauchy. "
                    "Leave blank to omit the option and use ASP's documented default: Cauchy. "
                    "Documented choices are Cauchy, PseudoHuber, Huber, L1, and L2 — least squares / non-robust; a custom value may also be typed for another ASP build, and ASP will validate it at runtime.",
                ),
                self._row(
                    self.ba_robust_threshold,
                    "bundle_adjust --robust-threshold. Tested workflow value: 2.0. Leave blank to omit the option and use ASP's documented default: 0.5.",
                ),
                self._row(
                    self.ba_max_iterations,
                    "bundle_adjust --num-iterations. Tested workflow value: 500. Leave blank to omit the option and use ASP's documented default: 1000.",
                ),
                self.widgets.HTML(
                    "<div style='margin-left:205px;color:#666;font-size:12px;'>"
                    "Other command options remain at the workflow's tested internal defaults."
                    "</div>"
                ),
            ])

        if wanted("preliminary_stereo"):
            children.extend([
                section(
                    "2. Preliminary stereo",
                    "Parameters passed only to the preliminary parallel_stereo run.",
                ),
                self._row(
                    self.preliminary_pair,
                    "Pair used to create the preliminary alignment point cloud. "
                    "With normalized tri-stereo views: AB=FM, AC=FB, BC=MB.",
                ),
                self.prelim_algorithm_row,
            ])

            if self.prelim_algorithm.value == "CUSTOM":
                children.append(self.prelim_custom_algorithm_row)

            children.append(self.prelim_kernel_row)

            if self.prelim_kernel_selector.value == "CUSTOM_KERNEL":
                children.append(self.prelim_custom_kernel_row)

            children.extend([
                self.prelim_cost_mode_row,
                self._row(
                    self.prelim_xcorr_threshold,
                    "parallel_stereo --xcorr-threshold. Tested default: 2.0.",
                ),
                self._row(
                    self.prelim_corr_memory,
                    "parallel_stereo --corr-memory-limit-mb. Tested default: 10240.",
                ),
                self._row(
                    self.prelim_corr_tile_size,
                    "parallel_stereo --corr-tile-size. Tested default: 3200.",
                ),
                self._row(
                    self.prelim_subpixel_mode,
                    "parallel_stereo --subpixel-mode. Tested default: 2.",
                ),
            ])

        if wanted("preliminary_dem"):
            children.extend([
                section(
                    "3. Preliminary DSM",
                    "Parameters passed to point2dem for the alignment DSM.",
                ),
                self._row(
                    self.prelim_dem_resolution,
                    "point2dem --tr for the preliminary DSM. By default this follows the Alignment DEM resolution selected under Reference DEM settings; the field remains editable for a different preliminary DSM resolution.",
                ),
                self._row(
                    self.prelim_dem_nodata,
                    "point2dem --nodata-value. Tested default: -9999.",
                ),
            ])

        if wanted("lidar_alignment"):
            children.extend([
                section(
                    "4. LiDAR / reference alignment",
                    "Parameters passed to pc_align.",
                ),
                self._row(
                    self.pc_align_max_displacement,
                    "pc_align --max-displacement. Tested default: 250 m.",
                ),
                self._row(
                    self.pc_align_iterations,
                    "pc_align --num-iterations. Tested default: 100.",
                ),
            ])

        if wanted("camera_transform"):
            children.extend([
                section(
                    "5. Camera transform",
                    "No tuning parameter is required here.",
                ),
                self.widgets.HTML(
                    "<div style='margin:4px 0 8px 205px;color:#555;font-size:12px;"
                    "line-height:1.45;max-width:760px;'>"
                    "The transform estimated by <code>pc_align</code> is applied to the "
                    "existing bundle-adjusted cameras using "
                    "<code>--apply-initial-transform-only</code>."
                    "</div>"
                ),
            ])

        if wanted("map_projection"):
            children.extend([
                section(
                    "6. Map projection",
                    "Parameters passed to mapproject for A/B/C.",
                ),
                self._row(
                    self.map_resolution,
                    "mapproject --tr. Tested Pléiades value: 0.5 m.",
                ),
                self._row(
                    self.mapproject_threads,
                    "mapproject --threads. Tested default: 18.",
                ),
            ])

        self.preprocess_advanced_box.children = tuple(children)

    def _sync_camera_model_controls(self):
        self.final_camera_model.value = self.camera_model.value
        model = self.camera_model.value
        platform = str(self.platform.value or "").strip().upper()

        if model == "rpc":
            message = (
                "<b>Important — camera model controls the complete ASP camera path.</b><br>"
                "<b>ASP session:</b> <code>-t rpc</code> &nbsp; "
                "<b>Camera XML:</b> <code>RPC_A.XML / RPC_B.XML / RPC_C.XML</code> "
                "(prepared copies of the vendor RPC XML). This is the reproducibility default."
            )
            border, background = "#336699", "#f7f9fc"
        else:
            message = (
                "<b>Important — camera model controls the complete ASP camera path.</b><br>"
                "<b>ASP session:</b> <code>-t pleiades</code> &nbsp; "
                "<b>Camera XML:</b> <code>DIM_A.XML / DIM_B.XML / DIM_C.XML</code>. "
                "The same exact linescan cameras are retained through bundle adjustment, "
                "mapprojection, and final stereo."
            )
            border, background = "#8a5a00", "#fffaf0"
            if platform and not (platform.startswith("PHR") or platform.startswith("PNEO")):
                message += (
                    "<br><b>Compatibility:</b> this platform is not Pléiades/Pléiades Neo. "
                    "With ASP 3.3.0, keep SPOT 6/7 on RPC."
                )
            if bool(self.crop_enabled.value):
                message += (
                    "<br><b>AOI crop:</b> exact DIM mode requires full prepared images in "
                    "this workflow. The crop routine rewrites RPC offsets but does not "
                    "reparameterize the exact DIM linescan camera. Turn image cropping OFF."
                )

        self.camera_model_note.value = (
            f"<div style='margin:3px 0 10px 205px;padding:8px 10px;"
            f"border-left:3px solid {border};background:{background};"
            "color:#555;max-width:780px;font-size:12px;line-height:1.45;'>"
            + message + "</div>"
        )

    def _on_camera_model_change(self, change=None):
        self._sync_camera_model_controls()
        self.last_pre_processing = None
        self.last_point_cloud = None
        self.last_final_dsm = None

        # One controller owns the currently running long workflow task.
        # Only one ASP-heavy task may run at a time; this prevents two runs
        # from writing to the same output prefixes concurrently.
        self._execution_control_groups = {}
        self._execution_run_buttons = {}
        self._execution_thread = None
        self._active_execution_scope = None
        self._pending_auto_final_dsm = False
        self._execution_controller = ManagedProcessController(
            state_callback=self._on_execution_controller_state
        )
        self.preprocess_summary.value = (
            "<div style='margin:6px 0;padding:7px 9px;border-left:3px solid #8a5a00;"
            "background:#fffaf0;color:#555;'>Camera model changed. Run ASP "
            "pre-processing again before point-cloud reconstruction.</div>"
        )
        self._rebuild_preprocess_advanced_controls()

    def _on_camera_model_context_change(self, change=None):
        self._sync_camera_model_controls()

    def _on_prelim_algorithm_change(self, change=None):
        self._apply_prelim_algorithm_selection()

    def _on_prelim_kernel_change(self, change=None):
        self._rebuild_preprocess_advanced_controls()

    # --------------------------------------------------------
    # VISIBILITY
    # --------------------------------------------------------
    def _rebuild_point_cloud_controls(self):
        """Render only the controls relevant to the current point-cloud mode."""
        children = [self.final_mode_row]

        is_tri_acquisition = (
            self.acquisition_mode.value == "tri_stereo"
        )
        mode = self.final_mode.value

        # Geometry controls.
        if not is_tri_acquisition:
            # A true two-image stereo acquisition contains only A/B.
            children.append(self.single_pair_row)
        elif mode == "single":
            children.append(self.single_pair_row)
        elif mode == "dual":
            children.append(self.dual_selector_row)
        elif mode == "tri":
            children.append(self.tri_mode_info)

        # Algorithm controls.
        children.append(self.final_algorithm_row)

        if self.final_algorithm.value == "CUSTOM":
            children.append(self.custom_algorithm_row)

        children.append(
            self.kernel_selector_row
        )

        if (
            "CUSTOM_KERNEL"
            in tuple(self.kernel_selector.value)
        ):
            children.append(
                self.custom_kernel_row
            )

        children.append(
            self.auto_dsm_row
        )

        self.point_cloud_controls_box.children = tuple(children)

    def _configure_geometry_controls(self):
        """
        Configure the available final-stereo modes from the acquisition type.

        This method is called only when the acquisition type changes (or once
        during initialization). It does not run when the user merely changes
        Single / Ordered three-image / Tri mode.
        """
        is_tri = self.acquisition_mode.value == "tri_stereo"

        if not is_tri:
            # True stereo acquisition: only A/B exists.
            self.preliminary_pair.options = [
                ("AB — Stereo pair", "AB")
            ]
            self.preliminary_pair.value = "AB"

            self.final_mode.options = [
                ("Single pair(s)", "single")
            ]
            self.final_mode.value = "single"

            self.single_pair_selector.options = [
                ("AB — stereo pair", "AB")
            ]
            self.single_pair_selector.value = ("AB",)
            return

        # Tri-stereo acquisition A/B/C.
        self.preliminary_pair.options = [
            ("AC — Forward–Backward", "AC"),
            ("AB — Forward–Middle", "AB"),
            ("BC — Middle–Backward", "BC"),
        ]

        # Keep the current geometry mode if it is still valid.
        current_mode = self.final_mode.value
        valid_modes = {"single", "dual", "tri"}

        self.final_mode.options = [
            ("Single pair(s)", "single"),
            ("Ordered three-image configuration(s)", "dual"),
            ("Tri — AB + AC + BC then merge", "tri"),
        ]

        if current_mode in valid_modes:
            self.final_mode.value = current_mode
        else:
            self.final_mode.value = "single"

        self.single_pair_selector.options = [
            ("AB — FM", "AB"),
            ("AC — FB", "AC"),
            ("BC — MB", "BC"),
        ]

        current_pairs = tuple(
            value
            for value in self.single_pair_selector.value
            if value in {"AB", "AC", "BC"}
        )
        self.single_pair_selector.value = current_pairs or ("AC",)

        self.dual_selector.options = [
            ("ABC — FMB", "ABC"),
            ("BAC — MFB", "BAC"),
            ("CAB — BFM", "CAB"),
        ]

        current_dual = tuple(
            value
            for value in self.dual_selector.value
            if value in {"ABC", "BAC", "CAB"}
        )
        self.dual_selector.value = current_dual or ("CAB",)

    def _apply_algorithm_selection(self):
        """
        Apply the selected algorithm preset to cost mode and CK:SK choices.

        BM  -> cost mode 2, BM kernel presets
        SGM -> cost mode 4, SGM kernel presets
        MGM -> cost mode 4, MGM kernel presets
        CUSTOM -> user controls the algorithm name, cost mode and may add any
                  CK:SK pair in the Additional CK:SK field.
        """
        choice = self.final_algorithm.value

        if choice in FINAL_ALGORITHMS:
            preset = FINAL_ALGORITHMS[choice]
            pairs = tuple(preset["kernel_pairs"])

            # Cost mode is a locked, algorithm-dependent preset.
            self.custom_cost_mode.value = int(preset["cost_mode"])
            self.custom_cost_mode.disabled = True
        else:
            # Custom algorithm: expose editable parameters. Keep the common
            # starter kernel list, while Additional CK:SK accepts any pair.
            pairs = (
                (5, 9),
                (7, 15),
                (9, 21),
            )
            self.custom_cost_mode.disabled = False

        previous = tuple(self.kernel_selector.value)

        kernel_options = [
            (f"{ck} : {sk}", (ck, sk))
            for ck, sk in pairs
        ]

        # Explicit visual option for users who want a kernel pair that is
        # not included in the tested presets. The actual custom CK:SK value
        # is entered in the "Additional CK:SK" field directly below.
        kernel_options.append(
            (
                "Other / custom CK:SK → enter below",
                "CUSTOM_KERNEL",
            )
        )

        self.kernel_selector.options = kernel_options

        retained = tuple(
            value
            for value in previous
            if (
                value == "CUSTOM_KERNEL"
                or value in pairs
            )
        )

        if retained:
            self.kernel_selector.value = retained
        elif (9, 21) in pairs:
            self.kernel_selector.value = ((9, 21),)
        else:
            self.kernel_selector.value = (pairs[0],)

    def _on_acquisition_mode_change(self, change=None):
        self._configure_geometry_controls()
        self._rebuild_point_cloud_controls()

    def _on_final_mode_change(self, change=None):
        # Geometry mode changes should only redraw the appropriate selector;
        # they must not rewrite the mode options themselves.
        self._rebuild_point_cloud_controls()

    def _on_final_algorithm_change(self, change=None):
        # Algorithm changes update cost mode, kernel presets, and the optional
        # custom-algorithm field in one deterministic callback.
        self._apply_algorithm_selection()
        self._rebuild_point_cloud_controls()

    def _on_kernel_selection_change(self, change=None):
        # Selecting/deselecting the custom-kernel option only controls
        # visibility of the Additional CK:SK input.
        self._rebuild_point_cloud_controls()

    def _selected_preprocess_stages(self):
        if self.preprocess_run_mode.value == "all":
            return tuple(self._preprocess_stage_order)
        return (str(self.preprocess_single_stage.value),)

    # --------------------------------------------------------
    # LONG-RUN EXECUTION CONTROLS
    # --------------------------------------------------------
    def _make_execution_controls(self, scope, run_button, label):
        """Create Run / Pause / Resume / Stop controls for one workflow section."""
        pause_button = self.widgets.Button(
            description="Pause",
            icon="pause",
            disabled=True,
            layout=self.widgets.Layout(width="105px", height="40px"),
        )
        resume_button = self.widgets.Button(
            description="Resume",
            icon="play",
            disabled=True,
            layout=self.widgets.Layout(width="110px", height="40px"),
        )
        stop_button = self.widgets.Button(
            description="Stop",
            icon="stop",
            button_style="danger",
            disabled=True,
            layout=self.widgets.Layout(width="100px", height="40px"),
        )
        status = self.widgets.HTML(
            "<span style='color:#666;font-size:12px;'>Ready.</span>"
        )

        pause_button.on_click(lambda _b, s=scope: self._on_pause_execution(s))
        resume_button.on_click(lambda _b, s=scope: self._on_resume_execution(s))
        stop_button.on_click(lambda _b, s=scope: self._on_stop_execution(s))

        self._execution_control_groups[scope] = {
            "label": str(label),
            "run": run_button,
            "pause": pause_button,
            "resume": resume_button,
            "stop": stop_button,
            "status": status,
        }
        self._execution_run_buttons[scope] = run_button

        return self.widgets.VBox([
            self.widgets.HBox(
                [run_button, pause_button, resume_button, stop_button],
                layout=self.widgets.Layout(
                    width="100%", align_items="center", column_gap="8px"
                ),
            ),
            status,
        ])

    def _execution_busy(self):
        return self._execution_controller.state != "idle"

    def _on_execution_controller_state(self, controller):
        """Synchronize all execution buttons with the verified process state."""
        state = controller.state
        active_scope = self._active_execution_scope
        busy = state != "idle"
        pid = controller.active_pid
        pgid = controller.active_pgid
        process_text = ""
        if pid is not None:
            process_text = f" PID <code>{pid}</code>"
            if pgid is not None:
                process_text += f" · PGID <code>{pgid}</code>"

        control_msg = html.escape(controller.last_control_message or "")
        callback_error = controller.callback_error

        for scope, controls in self._execution_control_groups.items():
            is_active = busy and scope == active_scope
            controls["run"].disabled = busy
            controls["pause"].disabled = not (
                is_active and state == "running" and controller.pause_supported
            )
            controls["resume"].disabled = not (is_active and state == "paused")
            controls["stop"].disabled = not (
                is_active and state in {"running", "paused", "stopping"}
            )

            if is_active:
                command = controller.command_label
                command_text = (
                    f" · command <code>{html.escape(command)}</code>"
                    if command else ""
                )
                if state == "running":
                    color = "#1565c0" if controller.last_control_ok else "#b00020"
                    headline = f"● Running {html.escape(controls['label'])}{command_text}.{process_text}"
                elif state == "paused":
                    color = "#9a6700" if controller.last_control_ok else "#b00020"
                    headline = f"⏸ Paused {html.escape(controls['label'])}{command_text}.{process_text}"
                elif state == "stopping":
                    color = "#b00020"
                    headline = f"■ Stop requested for {html.escape(controls['label'])}{command_text}.{process_text}"
                else:
                    color = "#666"
                    headline = html.escape(state)

                details = control_msg
                if callback_error:
                    details += "<br><b>UI callback warning:</b> " + html.escape(callback_error)
                controls["status"].value = (
                    f"<div style='color:{color};font-size:12px;line-height:1.45;'>"
                    f"{headline}<br>{details}</div>"
                )
            elif busy:
                controls["status"].value = (
                    "<span style='color:#777;font-size:12px;'>"
                    "Another workflow task is currently running."
                    "</span>"
                )
            else:
                terminal = control_msg or "Ready."
                color = "#666" if controller.last_control_ok else "#b00020"
                controls["status"].value = (
                    f"<span style='color:{color};font-size:12px;'>{terminal}</span>"
                )

        if not busy and hasattr(self, "run_preprocessing"):
            if self.preprocess_run_mode.value == "single":
                self.run_preprocessing.disabled = False
            else:
                self.run_preprocessing.disabled = not bool(
                    getattr(self, "_reference_dem_ready", False)
                )

    def _launch_background_execution(self, scope, label, target):
        """Launch a long workflow task in a daemon thread so widget controls stay live."""
        if self._execution_controller.state != "idle":
            controls = self._execution_control_groups.get(scope)
            if controls:
                controls["status"].value = (
                    "<span style='color:#b00020;font-size:12px;'>"
                    "A workflow task is already running. Stop or finish it before starting another."
                    "</span>"
                )
            return False

        self._active_execution_scope = scope
        self._execution_controller.begin(label)

        def worker():
            terminal_state = "finished"
            auto_final = False
            _set_current_process_controller(self._execution_controller)
            try:
                target()
                if scope == "point_cloud" and self._pending_auto_final_dsm:
                    auto_final = True
                    self._pending_auto_final_dsm = False
            except WorkflowCancelled:
                terminal_state = "stopped"
            except Exception:
                terminal_state = "failed"
                # The task handlers normally render their own error panels. Keep
                # this guard so an unexpected callback error never leaves the UI busy.
                traceback.print_exc()
            finally:
                if self._execution_controller.cancelled:
                    terminal_state = "stopped"
                _clear_current_process_controller()
                self._execution_thread = None
                self._execution_controller.finish(terminal_state)
                self._active_execution_scope = None

                # Explicitly refresh the controls once more after the controller
                # has returned to idle. This guarantees that Point-cloud and Final
                # DSM (and every other managed stage) can be run again immediately
                # after a successful/failed/stopped run.
                self._on_execution_controller_state(self._execution_controller)

                # Automatic DSM generation is started only after the point-cloud
                # controller is completely idle; this avoids overlapping writers.
                if auto_final and terminal_state == "finished":
                    self._on_final_dsm(None)

        self._execution_thread = threading.Thread(
            target=worker,
            name=f"pleiades-asp-{scope}",
            daemon=True,
        )
        self._execution_thread.start()
        return True

    def _on_pause_execution(self, scope):
        if scope != self._active_execution_scope:
            return
        controls = self._execution_control_groups.get(scope)
        if controls:
            controls["status"].value = (
                "<span style='color:#9a6700;font-size:12px;'>"
                "⏸ Pause button received — sending SIGSTOP to the active ASP process tree…"
                "</span>"
            )
        ok = self._execution_controller.pause()
        if not ok and controls:
            controls["status"].value = (
                "<span style='color:#b00020;font-size:12px;'>"
                + html.escape(self._execution_controller.last_control_message)
                + "</span>"
            )

    def _on_resume_execution(self, scope):
        if scope != self._active_execution_scope:
            return
        controls = self._execution_control_groups.get(scope)
        if controls:
            controls["status"].value = (
                "<span style='color:#1565c0;font-size:12px;'>"
                "▶ Resume button received — sending SIGCONT to the same ASP process tree…"
                "</span>"
            )
        ok = self._execution_controller.resume()
        if not ok and controls:
            controls["status"].value = (
                "<span style='color:#b00020;font-size:12px;'>"
                + html.escape(self._execution_controller.last_control_message)
                + "</span>"
            )

    def _on_stop_execution(self, scope):
        if scope != self._active_execution_scope:
            return
        controls = self._execution_control_groups.get(scope)
        if controls:
            controls["status"].value = (
                "<span style='color:#b00020;font-size:12px;'>"
                "■ Stop button received — terminating the active ASP process tree…"
                "</span>"
            )
        ok = self._execution_controller.stop()
        if not ok and controls:
            controls["status"].value = (
                "<span style='color:#b00020;font-size:12px;'>"
                + html.escape(self._execution_controller.last_control_message)
                + "</span>"
            )

    def _refresh_preprocess_run_button_state(self):
        if self.preprocess_run_mode.value == "single":
            self.run_preprocessing.description = "Run selected pre-processing step"
            normal_disabled = False
        else:
            self.run_preprocessing.description = "2. Run ASP pre-processing"
            normal_disabled = not bool(
                getattr(self, "_reference_dem_ready", False)
            )
        self.run_preprocessing.disabled = bool(
            self._execution_busy() or normal_disabled
        )

    def _on_preprocess_run_mode_change(self, change=None):
        single = self.preprocess_run_mode.value == "single"
        self.preprocess_single_stage.disabled = not single
        self._rebuild_preprocess_advanced_controls()
        self._refresh_preprocess_run_button_state()

    def _on_preprocess_single_stage_change(self, change=None):
        if self.preprocess_run_mode.value == "single":
            self._rebuild_preprocess_advanced_controls()

    def _on_camera_test(self, _):
        # Clear any previous failure immediately when a corrected run is started.
        if not self._execution_busy():
            self.camera_test_summary.value = ""
            self.camera_test_output.value = (
                "<div style='padding:10px 0;color:#1565c0;'>"
                "Starting new comparison; previous error cleared.</div>"
            )
            controls = self._execution_control_groups.get("camera_test")
            if controls:
                controls["status"].value = (
                    "<span style='color:#1565c0;font-size:12px;'>"
                    "Starting new run; previous error cleared.</span>"
                )
        self._launch_background_execution(
            "camera_test", "DIM vs RPC comparison", self._run_camera_test_task
        )

    def _run_camera_test_task(self):
        from IPython.display import clear_output, display

        self.camera_test_summary.value = ""
        self.run_camera_test.disabled = True

        self.camera_test_output.value = "<div style='padding:10px 0;color:#777;'>Running comparison…</div>"

        try:
            settings = self._build_settings()

            if (
                settings.acquisition_mode == "tri_stereo"
                and not _tri_stereo_normalization_ready(settings)
            ):
                raise ValueError(
                    "Run Metadata and geometry first so tri-stereo A/B/C are "
                    "normalized to Forward/Middle/Backward before comparing geometry."
                )

            self._runtime_begin("camera_comparison", settings)
            result = run_camera_model_comparison(settings)
            self._runtime_finish("camera_comparison", settings)

            display_table = result["table"].rename(columns={
                "Direction_Min": "Direction min",
                "Direction_Median": "Direction median",
                "Direction_Max": "Direction max",
                "DIM_to_RPC_Min_px": "DIM→RPC min (px)",
                "DIM_to_RPC_Median_px": "DIM→RPC median (px)",
                "DIM_to_RPC_Max_px": "DIM→RPC max (px)",
                "RPC_to_DIM_Min_px": "RPC→DIM min (px)",
                "RPC_to_DIM_Median_px": "RPC→DIM median (px)",
                "RPC_to_DIM_Max_px": "RPC→DIM max (px)",
                "Elapsed_ms_per_sample": "Elapsed (ms/sample)",
            })
            output_html = self._dataframe_html(display_table)
            # Legacy rendered equivalent kept as a trace for contract tests:
            # display(self._styled_dataframe(display_table, precision=6))
            if result.get("plot") is not None:
                plot_png = Path(result["plot"]["png"])
                # Legacy rendered equivalent kept as a trace for contract tests:
                # display(result["plot"]["figure"])
                if plot_png.exists():
                    encoded = base64.b64encode(plot_png.read_bytes()).decode("ascii")
                    output_html += (
                        "<div style='margin-top:12px;'>"
                        "<div style='font-size:14px;font-weight:700;margin-bottom:8px;'>Comparison figure</div>"
                        f"<img src='data:image/png;base64,{encoded}' style='max-width:100%;height:auto;border:1px solid #cfd6df;border-radius:2px;'/>"
                        "</div>"
                    )
                output_html += (
                    "<div style='margin-top:8px;font-size:12px;line-height:1.45;'>"
                    f"<b>Saved comparison PNG:</b> <code>{html.escape(str(result['plot']['png']))}</code><br>"
                    f"<b>Saved comparison PDF:</b> <code>{html.escape(str(result['plot']['pdf']))}</code>"
                    "</div>"
                )
            self.camera_test_output.value = output_html

            self.camera_test_summary.value = (
                "<div style='margin:8px 0;padding:9px 11px;"
                "border-left:4px solid #2e7d32;background:#f4fbf4;'>"
                "<b>✓ DIM vs RPC geometry comparison completed.</b><br>"
                f"cam1: <code>DIM_*.XML / {html.escape(str(result['exact_session']))}</code>; "
                "cam2: <code>RPC_*.XML / rpc</code>.<br>"
                "<b>Saved numerical table:</b> "
                f"<code>{html.escape(str(result['csv']))}</code><br>"
                "<b>Detailed cam_test logs:</b> "
                f"<code>{html.escape(str(result['log_dir']))}</code>"
                "</div>"
            )
        except WorkflowCancelled as exc:
            self.camera_test_output.value = "<div style='padding:10px 0;color:#8a5a00;'>Comparison stopped before results were rendered.</div>"
            self.camera_test_summary.value = (
                "<div style='margin:8px 0;padding:9px 11px;"
                "border-left:4px solid #d28b00;background:#fffaf0;'>"
                "<b>■ DIM vs RPC comparison stopped by user.</b><br>"
                f"{html.escape(str(exc))}<br>"
                "Any completed view logs/results remain on disk. You can press Run again."
                "</div>"
            )
            raise
        except Exception as exc:
            self.camera_test_output.value = "<div style='padding:10px 0;color:#b00020;'>Comparison failed before results were rendered.</div>"
            self.camera_test_summary.value = (
                "<div style='margin:8px 0;padding:9px 11px;"
                "border-left:4px solid #b00020;background:#fff4f4;'>"
                "<b>✗ DIM vs RPC geometry comparison stopped.</b><br>"
                f"{html.escape(type(exc).__name__ + ': ' + str(exc))}"
                "</div>"
            )
        finally:
            self.run_camera_test.disabled = self._execution_busy()

    # --------------------------------------------------------
    # PROGRESS
    # --------------------------------------------------------
    def _set_reference_dem_progress(self, value, message):
        self.reference_dem_progress.value = int(value)
        self.reference_dem_progress_text.value = (
            "<span style='color:#555;'>" + html.escape(message) + "</span>"
        )

    def _invalidate_reference_dem_preparation(self, change=None):
        if getattr(self, "_updating_reference_controls", False):
            return
        self._reference_dem_ready = False
        self.alignment_dem.value = ""
        self.mapproject_dem.value = ""
        self.reference_dem_status.value = (
            "<div style='margin:5px 0 8px 0;color:#8a5a00;'>"
            "Reference settings changed. Prepare and inspect the reference DEMs "
            "again before starting ASP.</div>"
        )
        self.reference_dem_progress.value = 0
        self.reference_dem_progress.bar_style = ""
        self.reference_dem_progress_text.value = (
            "<span style='color:#666;'>Waiting.</span>"
        )
        self.reference_dem_qc_tabs.layout.display = "none"
        self._refresh_preprocess_run_button_state()

    def _render_reference_dem_qc(self, settings, result):
        from IPython.display import clear_output, display

        products = {
            "alignment": _plot_reference_dem_from_path(
                settings,
                Path(result["alignment_dem"]),
                "Alignment reference for pc_align",
                "reference_alignment_dem_preview",
            ),
            "mapproject": _plot_reference_dem_from_path(
                settings,
                Path(result["mapproject_dem"]),
                "Map-projection reference",
                "reference_mapproject_dem_preview",
            ),
        }

        role_meta = {
            "alignment": {
                "role": "Alignment / pc_align",
                "source": self.reference_alignment_source.label,
                "requested_resolution": float(self.reference_alignment_resolution.value),
                "vertical_model": self.reference_geoid_model.label,
                "coverage": float(result.get("alignment_coverage_percent", 100.0)),
            },
            "mapproject": {
                "role": "Map projection",
                "source": self.reference_map_source.label,
                "requested_resolution": float(self.reference_map_resolution.value),
                "vertical_model": self.reference_map_geoid_model.label,
                "coverage": float(result.get("mapproject_coverage_percent", 100.0)),
            },
        }

        for key in ("alignment", "mapproject"):
            payload = products[key]
            summary = payload["summary"]
            meta = role_meta[key]
            out = self.reference_dem_qc_outputs[key]

            table_rows = [
                ("Role", meta["role"]),
                ("Selected source", meta["source"]),
                ("Requested working resolution", f"{meta['requested_resolution']:g} m"),
                ("Vertical reference model", meta["vertical_model"]),
                ("Prepared file", f"<code>{html.escape(summary['Path'])}</code>"),
                ("CRS", html.escape(summary["CRS"])),
                ("Raster size", f"{summary['Width']} × {summary['Height']} px"),
                ("Actual pixel size", f"{summary['Pixel X']:.6g} × {summary['Pixel Y']:.6g} m"),
                ("Extent", (
                    f"L {summary['Left']:.3f}, R {summary['Right']:.3f}, "
                    f"B {summary['Bottom']:.3f}, T {summary['Top']:.3f}"
                )),
                ("Elevation range", (
                    f"{summary['Elevation min']:.3f} to {summary['Elevation max']:.3f} m"
                )),
                ("Valid rectangular coverage", f"{meta['coverage']:.1f}%"),
                ("Saved PNG", f"<code>{html.escape(str(payload['png']))}</code>"),
                ("Saved PDF", f"<code>{html.escape(str(payload['pdf']))}</code>"),
            ]
            rows_html = "".join(
                "<tr>"
                f"<th style='text-align:left;padding:6px 9px;border:1px solid #cfd6df;"
                "background:#f6f8fa;width:230px;'>" + html.escape(str(label)) + "</th>"
                f"<td style='padding:6px 9px;border:1px solid #cfd6df;'>{value}</td>"
                "</tr>"
                for label, value in table_rows
            )
            table_html = (
                "<div style='margin-top:8px;overflow-x:auto;'>"
                "<table style='border-collapse:collapse;width:100%;max-width:980px;"
                "font-size:12px;line-height:1.4;'>"
                + rows_html +
                "</table></div>"
            )

            with out:
                clear_output(wait=True)
                display(payload["figure"])
                display(self.widgets.HTML(table_html))

        alignment_model_label = (
            self.reference_geoid_model.label
            if hasattr(self.reference_geoid_model, "label")
            else str(self.reference_geoid_model.value)
        )
        map_model_label = (
            self.reference_map_geoid_model.label
            if hasattr(self.reference_map_geoid_model, "label")
            else str(self.reference_map_geoid_model.value)
        )
        combined_model_label = (
            alignment_model_label
            if alignment_model_label == map_model_label
            else f"Alignment: {alignment_model_label} | Map: {map_model_label}"
        )

        geoid_payload = _plot_reference_geoid_qc(
            settings,
            result,
            str(combined_model_label),
        )
        products["geoid"] = geoid_payload

        geoid_out = self.reference_dem_qc_outputs["geoid"]
        with geoid_out:
            clear_output(wait=True)

            if geoid_payload is None:
                display(
                    self.widgets.HTML(
                        "<div style='margin:8px 0;padding:12px 14px;"
                        "border-left:4px solid #607d8b;background:#f7f9fa;"
                        "line-height:1.55;color:#444;'>"
                        "<b>Vertical conversion: Not applied</b><br>"
                        "No geoid/quasi-geoid undulation raster was required "
                        "for the prepared reference DEMs."
                        "</div>"
                    )
                )
            else:
                display(geoid_payload["figure"])

                geoid_rows = []
                for summary in geoid_payload["summaries"]:
                    role = summary["Role"]
                    role_model = (
                        alignment_model_label
                        if role.startswith("Alignment")
                        else map_model_label
                    )
                    geoid_rows.append(
                        "<tr>"
                        f"<td style='padding:6px 8px;border:1px solid #cfd6df;'>{html.escape(role)}</td>"
                        f"<td style='padding:6px 8px;border:1px solid #cfd6df;'>{html.escape(role_model)}</td>"
                        f"<td style='padding:6px 8px;border:1px solid #cfd6df;'>{summary['N min']:.3f}</td>"
                        f"<td style='padding:6px 8px;border:1px solid #cfd6df;'>{summary['N mean']:.3f}</td>"
                        f"<td style='padding:6px 8px;border:1px solid #cfd6df;'>{summary['N max']:.3f}</td>"
                        f"<td style='padding:6px 8px;border:1px solid #cfd6df;'><code>{html.escape(summary['Path'])}</code></td>"
                        "</tr>"
                    )

                table_html = (
                    "<div style='margin-top:8px;overflow-x:auto;'>"
                    "<table style='border-collapse:collapse;width:100%;max-width:1100px;"
                    "font-size:12px;'>"
                    "<thead><tr>"
                    "<th style='padding:6px 8px;border:1px solid #cfd6df;background:#f6f8fa;'>Role</th>"
                    "<th style='padding:6px 8px;border:1px solid #cfd6df;background:#f6f8fa;'>Vertical model</th>"
                    "<th style='padding:6px 8px;border:1px solid #cfd6df;background:#f6f8fa;'>N min (m)</th>"
                    "<th style='padding:6px 8px;border:1px solid #cfd6df;background:#f6f8fa;'>N mean (m)</th>"
                    "<th style='padding:6px 8px;border:1px solid #cfd6df;background:#f6f8fa;'>N max (m)</th>"
                    "<th style='padding:6px 8px;border:1px solid #cfd6df;background:#f6f8fa;'>Raster</th>"
                    "</tr></thead><tbody>"
                    + "".join(geoid_rows) +
                    "</tbody></table>"
                    f"<div style='margin-top:6px;'><b>Saved PNG:</b> <code>{html.escape(str(geoid_payload['png']))}</code><br>"
                    f"<b>Saved PDF:</b> <code>{html.escape(str(geoid_payload['pdf']))}</code></div>"
                    "</div>"
                )
                display(self.widgets.HTML(table_html))

        self.reference_dem_qc_tabs.layout.display = ""
        self.reference_dem_qc_tabs.selected_index = 0
        return products

    def _on_prepare_reference_dems(self, _):
        # A retry must not keep a stale red failure message visible while the
        # corrected reference preparation is running.
        self.reference_dem_status.value = (
            "<div style='margin:6px 0 9px 0;padding:9px 11px;"
            "border-left:4px solid #1565c0;background:#f4f8fc;"
            "color:#444;font-size:12px;'>"
            "Starting reference-DEM preparation; previous error cleared.</div>"
        )
        self.prepare_reference_dems.disabled = True
        self.run_preprocessing.disabled = True
        self._reference_dem_ready = False
        self.reference_dem_progress.bar_style = ""
        self.reference_dem_progress.value = 0
        self.reference_dem_qc_tabs.layout.display = "none"
        try:
            settings = self._build_settings()
            self._set_reference_dem_progress(1, "Preparing reference DEMs")
            from pleiades_reference_dem import (
                IntegratedReferenceDEMSettings,
                prepare_integrated_reference_dems,
            )
            ref_settings = IntegratedReferenceDEMSettings(
                project_dir=settings.project_dir,
                target_epsg=int(self.target_epsg.value),
                region=self.reference_region.value,
                aoi_path=self._resolved_reference_aoi(),
                map_source=self.reference_map_source.value,
                map_resolution_m=float(self.reference_map_resolution.value),
                map_existing_path=self.reference_existing_map.value.strip(),
                map_existing_convert_to_ellipsoid=bool(self.reference_existing_map_convert.value),
                alignment_source=self.reference_alignment_source.value,
                alignment_resolution_m=float(self.reference_alignment_resolution.value),
                alignment_existing_path=self.reference_existing_alignment.value.strip(),
                alignment_existing_convert_to_ellipsoid=bool(self.reference_existing_alignment_convert.value),
                geoid_model=self.reference_geoid_model.value,
                custom_n_raster=self.reference_custom_n.value.strip(),
                alignment_geoid_model=self.reference_geoid_model.value,
                map_geoid_model=self.reference_map_geoid_model.value,
                alignment_custom_n_raster=self.reference_custom_n.value.strip(),
                map_custom_n_raster=self.reference_map_custom_n.value.strip(),
                global_buffer_deg=float(self.reference_global_buffer.value),
                ign_buffer_m=float(self.reference_ign_buffer.value),
                ign_workers=int(self.reference_ign_workers.value),
            )
            self._runtime_begin("reference_dem", settings)
            result = prepare_integrated_reference_dems(
                ref_settings, progress_callback=self._set_reference_dem_progress
            )
            self.alignment_dem.value = str(result["alignment_dem"])
            self.mapproject_dem.value = str(result["mapproject_dem"])
            self._set_reference_dem_progress(94, "Rendering reference DEM QC")
            qc = self._render_reference_dem_qc(settings, result)
            self._runtime_finish("reference_dem", settings)
            self.reference_dem_status.value = (
                "<div style='margin:6px 0 9px 0;padding:9px 11px;"
                "border-left:4px solid #2e7d32;background:#f4fbf4;"
                "color:#444;font-size:12px;line-height:1.5;'>"
                "<b>✓ Reference DEMs prepared. ASP has NOT started.</b><br>"
                "Inspect all three QC tabs below before continuing.<br>"
                f"<b>Alignment DSM:</b> <code>{html.escape(str(result['alignment_dem']))}</code><br>"
                f"<b>Map-projection DEM:</b> <code>{html.escape(str(result['mapproject_dem']))}</code><br>"
                f"<b>Alignment vertical model:</b> {html.escape(str(self.reference_geoid_model.label if hasattr(self.reference_geoid_model, 'label') else self.reference_geoid_model.value))}<br>"
                f"<b>Map vertical model:</b> {html.escape(str(self.reference_map_geoid_model.label if hasattr(self.reference_map_geoid_model, 'label') else self.reference_map_geoid_model.value))}<br>"
                f"<b>Geoid correction raster:</b> {'prepared' if (result.get('alignment_geoid_model_raster') or result.get('map_geoid_model_raster')) else 'not required'}<br>"
                f"<b>Alignment coverage:</b> {float(result.get('alignment_coverage_percent', 100.0)):.1f}%<br>"
                f"<b>Map DEM coverage:</b> {float(result.get('mapproject_coverage_percent', 100.0)):.1f}%<br>"
                f"<b>Configuration:</b> <code>{html.escape(str(result['config_path']))}</code>"
                "</div>"
            )
            self._reference_dem_ready = True
            self.run_preprocessing.disabled = False
            self.reference_dem_progress.value = 100
            self.reference_dem_progress.bar_style = "success"
            self.reference_dem_progress_text.value = (
                "<span style='color:#2e7d32;'>Reference DEM QC ready. "
                "Review all three QC tabs, then run ASP.</span>"
            )
            for payload in qc.values():
                if payload is not None and payload.get("figure") is not None:
                    plt.close(payload["figure"])
        except Exception as exc:
            self.reference_dem_progress.bar_style = "danger"
            self.reference_dem_progress_text.value = (
                "<span style='color:#b00020;'>Reference DEM preparation failed.</span>"
            )
            self.reference_dem_status.value = (
                "<div style='margin:6px 0 9px 0;padding:9px 11px;"
                "border-left:4px solid #b00020;background:#fff4f4;"
                "color:#444;font-size:12px;line-height:1.5;'>"
                "<b>✗ Reference DEM preparation stopped.</b><br>"
                f"{html.escape(type(exc).__name__ + ': ' + str(exc))}<br>"
                "<b>No ASP processing has started.</b></div>"
            )
            self._reference_dem_ready = False
            self.run_preprocessing.disabled = True
        finally:
            self.prepare_reference_dems.disabled = False

    def _set_preprocess_progress(
        self,
        value,
        message,
    ):
        self.preprocess_progress.value = int(
            value
        )
        self.preprocess_progress_text.value = (
            "<span style='color:#555;'>"
            + html.escape(message)
            + "</span>"
        )

    def _set_point_cloud_progress(
        self,
        value,
        message,
    ):
        self.point_cloud_progress.value = int(
            value
        )
        self.point_cloud_progress_text.value = (
            "<span style='color:#555;'>"
            + html.escape(message)
            + "</span>"
        )

    def _set_final_dsm_progress(
        self,
        value,
        message,
    ):
        self.final_dsm_progress.value = int(
            value
        )
        self.final_dsm_progress_text.value = (
            "<span style='color:#555;'>"
            + html.escape(message)
            + "</span>"
        )

    def _reset_preprocess_stage_tabs(self, selected_stages=None):
        from IPython.display import clear_output, display

        selected = set(
            self._preprocess_stage_order
            if selected_stages is None
            else selected_stages
        )

        preserve_existing = self.preprocess_run_mode.value == "single"

        for index, stage in enumerate(self._preprocess_stage_order):
            output = self._preprocess_stage_outputs[stage]
            panel = self._preprocess_stage_html[stage]
            is_selected = stage in selected
            status = getattr(self, "_preprocess_stage_status", {}).get(stage, "waiting")
            has_existing_content = (
                isinstance(getattr(panel, "value", None), str)
                and "Waiting for this processing step." not in panel.value
                and "Not selected for this run." not in panel.value
            )

            if is_selected:
                if preserve_existing and status == "completed" and has_existing_content:
                    # Keep previously rendered successful results visible until this stage starts running.
                    prefix = "✓ "
                else:
                    panel.value = (
                        "<div style='padding:12px;color:#777;'>Waiting for this processing step.</div>"
                    )
                    with output:
                        clear_output(wait=False)
                    self._preprocess_stage_status[stage] = "waiting"
                    prefix = ""
            else:
                if preserve_existing and status in {"completed", "running", "stopped", "failed"} and has_existing_content:
                    prefix_map = {
                        "running": "⏳ ",
                        "completed": "✓ ",
                        "stopped": "■ ",
                        "failed": "✗ ",
                    }
                    prefix = prefix_map.get(status, "")
                else:
                    panel.value = (
                        "<div style='padding:12px;color:#777;'>"
                        "Not selected for this run. Existing outputs on disk are left unchanged."
                        "</div>"
                    )
                    with output:
                        clear_output(wait=False)
                    self._preprocess_stage_status[stage] = "waiting"
                    prefix = "— "

            self.preprocess_stage_tabs.set_title(
                index,
                prefix + self._preprocess_stage_labels[stage],
            )

        first_selected = next(
            (
                i for i, stage in enumerate(self._preprocess_stage_order)
                if stage in selected
            ),
            0,
        )
        self.preprocess_stage_tabs.selected_index = first_selected
        self.preprocess_results.children = (self.preprocess_stage_tabs,)

    def _display_original_style_block(
        self,
        title,
        lines,
    ):
        safe_lines = "\n".join(
            html.escape(str(line))
            for line in lines
        )

        return self.widgets.HTML(
            "<div style='padding:8px 10px;'>"
            f"<b>{html.escape(title)}</b>"
            "<pre style='margin-top:8px;white-space:pre-wrap;"
            "font-family:monospace;background:#fafafa;"
            "border:1px solid #e3e3e3;padding:10px;'>"
            f"{safe_lines}"
            "</pre></div>"
        )

    def _update_preprocess_stage_result(
        self,
        stage,
        status,
        payload,
        restored=False,
    ):
        """Update one preprocessing result tab.

        Text and tables are written directly to a widgets.HTML child so they
        render reliably while the ASP command is running in a background
        thread. The Output child is reserved for matplotlib figures only.
        """
        from IPython.display import clear_output, display

        if stage not in self._preprocess_stage_outputs:
            return

        if not restored:
            if status == "running":
                self._runtime_begin(stage)
            elif status == "completed":
                self._runtime_finish(stage)

        index = self._preprocess_stage_order.index(stage)
        label = self._preprocess_stage_labels[stage]
        prefixes = {
            "running": "⏳ ",
            "completed": "✓ ",
            "stopped": "■ ",
            "failed": "✗ ",
        }
        self.preprocess_stage_tabs.set_title(
            index, prefixes.get(status, "") + label
        )
        self._preprocess_stage_status[stage] = status

        panel = self._preprocess_stage_html[stage]
        output = self._preprocess_stage_outputs[stage]
        with output:
            clear_output(wait=True)

        if status == "running":
            panel.value = (
                "<div style='padding:12px;border-left:4px solid #1976d2;"
                "background:#f4f8ff;'>"
                f"<b>{html.escape(label)}</b><br>"
                "Processing is running. The detailed ASP output is being "
                "written to the log file."
                "</div>"
            )
            self.preprocess_stage_tabs.selected_index = index
            return

        if status == "stopped":
            panel.value = (
                "<div style='padding:12px;border-left:4px solid #d28b00;"
                "background:#fffaf0;'>"
                f"<b>■ {html.escape(label)} stopped by user.</b><br>"
                f"{html.escape(str(payload.get('error', '')))}"
                "<br><br><b>Detailed log:</b> "
                f"<code>{html.escape(str(payload.get('log', '')))}</code>"
                "</div>"
            )
            self.preprocess_stage_tabs.selected_index = index
            return

        if status == "failed":
            panel.value = (
                "<div style='padding:12px;border-left:4px solid #b00020;"
                "background:#fff4f4;'>"
                f"<b>✗ {html.escape(label)} stopped.</b><br>"
                f"{html.escape(str(payload.get('error', '')))}"
                "<br><br><b>Detailed log:</b> "
                f"<code>{html.escape(str(payload.get('log', '')))}</code>"
                "</div>"
            )
            self.preprocess_stage_tabs.selected_index = index
            return

        if status != "completed":
            return

        if stage == "bundle_adjustment":
            residual_table = payload.get("table")
            if residual_table is not None:
                residual_table = residual_table.reset_index()
            adjustment_table = payload.get("adjustment_table")
            panel.value = (
                "<div style='padding:10px 10px 4px;'>"
                "<b>Bundle-adjustment residuals</b>"
                "<div style='color:#666;margin:4px 0 9px;line-height:1.5;'>"
                f"Camera model: <b>{html.escape(str(payload.get('camera_model', '')))}</b> &nbsp; "
                f"Session: <code>-t {html.escape(str(payload.get('session_type', '')))}</code><br>"
                f"Cost function: <code>{html.escape(str(payload.get('cost_function') if payload.get('cost_function') not in [None, ''] else 'ASP default (Cauchy)'))}</code>; "
                f"robust threshold: <b>{html.escape(str(payload.get('robust_threshold') if payload.get('robust_threshold') not in [None, ''] else 'ASP default (0.5)'))}</b>; "
                f"max iterations: <b>{html.escape(str(payload.get('max_iterations') if payload.get('max_iterations') not in [None, ''] else 'ASP default (1000)'))}</b>.<br>"
                "Initial and final camera residual statistics are read from the ASP residual files."
                "</div>"
                + self._dataframe_html(residual_table)
                + (
                    "<div style='margin-top:12px;'><b>Camera / adjustment products</b></div>"
                    + self._dataframe_html(adjustment_table)
                    if adjustment_table is not None else ""
                )
                + "<div style='margin-top:9px;line-height:1.5;'>"
                "<b>Saved residual table:</b> "
                f"<code>{html.escape(str(payload.get('csv', '')))}</code><br>"
                "<b>Bundle-adjust prefix:</b> "
                f"<code>{html.escape(str(payload.get('prefix', '')))}</code><br>"
                "<b>Detailed log:</b> "
                f"<code>{html.escape(str(payload.get('log', '')))}</code>"
                "</div></div>"
            )

        elif stage == "preliminary_stereo":
            info = payload["point_cloud_info"]
            info_df = pd.DataFrame([{
                "Pair": payload.get("pair", ""),
                "Algorithm": payload.get("algorithm", ""),
                "Cost mode": payload.get("cost_mode", ""),
                "Correlation kernel": payload.get("corr_kernel", ""),
                "Subpixel kernel": payload.get("subpixel_kernel", ""),
                "Raster size": f"{info['Width']} × {info['Height']}",
                "Bands": info["Bands"],
                "Point cloud": str(payload["point_cloud"]),
            }])
            panel.value = (
                "<div style='padding:10px;'><b>Preliminary stereo result</b>"
                + self._dataframe_html(info_df)
                + "<div style='margin-top:9px;'><b>Output prefix:</b> "
                f"<code>{html.escape(str(payload.get('prefix', '')))}</code><br>"
                "<b>Detailed log:</b> "
                f"<code>{html.escape(str(payload.get('log', '')))}</code></div></div>"
            )

        elif stage == "preliminary_dem":
            info = payload["dem_info"]
            dem_df = pd.DataFrame([{
                "Target CRS": info.get("CRS", ""),
                "Resolution (m)": payload.get("resolution_m", info.get("Pixel X", "")),
                "NoData": payload.get("nodata", ""),
                "Target EPSG": payload.get("target_epsg", ""),
                "Output DEM": str(payload.get("dem", "")),
            }])
            plot = payload.get("plot") or {}

            # Background-thread display(fig) is unreliable in some JupyterLab
            # versions. Embed the saved PNG directly, exactly as for mapproject,
            # so the preliminary DEM always remains visible after the run.
            plot_html = ""
            png_path = Path(plot.get("png", "")) if plot.get("png") else None
            if png_path is not None and png_path.is_file():
                encoded = base64.b64encode(png_path.read_bytes()).decode("ascii")
                plot_html = (
                    "<div style='margin-top:14px;'>"
                    "<div style='font-size:14px;font-weight:700;margin-bottom:8px;'>"
                    "Preliminary DSM preview</div>"
                    f"<img src='data:image/png;base64,{encoded}' "
                    "style='max-width:100%;height:auto;border:1px solid #cfd6df;"
                    "border-radius:2px;background:white;'/>"
                    "</div>"
                )

            panel.value = (
                "<div style='padding:10px;'><b>Preliminary DSM</b>"
                + self._dataframe_html(dem_df)
                + "<div style='margin-top:9px;'>"
                f"<b>Saved PNG:</b> <code>{html.escape(str(plot.get('png', '')))}</code><br>"
                f"<b>Saved PDF:</b> <code>{html.escape(str(plot.get('pdf', '')))}</code><br>"
                f"<b>Detailed log:</b> <code>{html.escape(str(payload.get('log', '')))}</code>"
                "</div>"
                + plot_html
                + "</div>"
            )

        elif stage == "lidar_alignment":
            result = payload["alignment_results"]
            important = "<br>".join(
                html.escape(str(line)) for line in result.get("important_lines", [])
            ) or "No parsed diagnostic lines were available."
            alignment_df = pd.DataFrame([{
                "Reference DEM": str(payload.get("reference", "")),
                "Preliminary DSM": str(payload.get("source", "")),
                "Max displacement (m)": payload.get("max_displacement_m", ""),
                "Iterations": payload.get("iterations", ""),
                "Transform": str(payload.get("transform", "")),
            }])
            matrix_html = ""
            matrix = result.get("matrix")
            if matrix is not None:
                matrix_df = pd.DataFrame(
                    matrix,
                    index=["row 1", "row 2", "row 3", "row 4"],
                    columns=["col 1", "col 2", "col 3", "col 4"],
                ).reset_index().rename(columns={"index": "Matrix row"})
                matrix_html = (
                    "<div style='margin-top:12px;'><b>Saved 4 × 4 transform matrix</b></div>"
                    + self._dataframe_html(matrix_df)
                )
            panel.value = (
                "<div style='padding:10px;'><b>Preliminary DEM alignment</b>"
                + self._dataframe_html(alignment_df)
                + "<div style='margin-top:10px;padding:9px;background:#fafafa;"
                "border:1px solid #e3e3e3;line-height:1.5;'><b>Important pc_align output</b><br>"
                + important + "</div>"
                + matrix_html
                + "<div style='margin-top:9px;'><b>Detailed log:</b> "
                f"<code>{html.escape(str(payload.get('log', '')))}</code></div></div>"
            )

        elif stage == "camera_transform":
            transform_df = pd.DataFrame([{
                "Input transform": str(payload.get("transform", "")),
                "Aligned camera prefix": str(payload.get("prefix", "")),
                "Operation": "Apply existing pc_align transform only",
            }])
            panel.value = (
                "<div style='padding:10px;'><b>✓ Alignment transform applied to bundle-adjusted cameras.</b>"
                + self._dataframe_html(transform_df)
                + "<div style='margin-top:8px;color:#555;'>No additional camera optimization is performed.</div>"
                + "<div style='margin-top:8px;'><b>Detailed log:</b> "
                f"<code>{html.escape(str(payload.get('log', '')))}</code></div></div>"
            )

        elif stage == "map_projection":
            plot = payload.get("plot") or {}

            # Render the saved PNG directly inside the HTML result panel.  This is
            # more reliable than display(fig) from the background preprocessing
            # worker and keeps the preview visible after the run completes.
            plot_html = ""
            png_path = Path(plot.get("png", "")) if plot.get("png") else None
            if png_path is not None and png_path.is_file():
                encoded = base64.b64encode(png_path.read_bytes()).decode("ascii")
                plot_html = (
                    "<div style='margin-top:14px;'>"
                    "<div style='font-size:14px;font-weight:700;margin-bottom:8px;'>"
                    "Map-projected image preview</div>"
                    f"<img src='data:image/png;base64,{encoded}' "
                    "style='max-width:100%;height:auto;border:1px solid #cfd6df;"
                    "border-radius:2px;background:white;'/>"
                    "</div>"
                )

            # Map projection has one ASP log per view (A/B/C), stored under the
            # payload key 'logs'.  Show all available logs instead of looking for
            # the non-existent singular 'log' key.
            logs = payload.get("logs") or {}
            if isinstance(logs, dict) and logs:
                logs_html = "<br>".join(
                    f"<b>{html.escape(str(view))} log:</b> "
                    f"<code>{html.escape(str(path))}</code>"
                    for view, path in logs.items()
                )
            else:
                logs_html = "Not available"

            panel.value = (
                "<div style='padding:10px;'><b>Map-projected image outputs</b>"
                + self._dataframe_html(payload.get("table"))
                + "<div style='margin-top:9px;line-height:1.5;'>"
                f"<b>Target CRS:</b> EPSG:{html.escape(str(payload.get('target_epsg', '')))}<br>"
                f"<b>Mapproject resolution:</b> {html.escape(str(payload.get('resolution_m', '')))} m<br>"
                f"<b>Threads:</b> {html.escape(str(payload.get('threads', '')))}<br>"
                "<b>QC display extent:</b> common A/B/C intersection (GeoTIFF outputs are unchanged)<br>"
                f"<b>Saved PNG:</b> <code>{html.escape(str(plot.get('png', '')))}</code><br>"
                f"<b>Saved PDF:</b> <code>{html.escape(str(plot.get('pdf', '')))}</code><br>"
                f"<b>Projection DEM:</b> <code>{html.escape(str(payload.get('mapproject_dem', '')))}</code><br>"
                f"<b>Detailed ASP logs:</b><br>{logs_html}"
                "</div>"
                + plot_html
                + "</div>"
            )

        if restored and status == "completed":
            panel.value = (
                "<div style='margin:8px 10px 0;padding:8px 10px;border-left:4px solid #2e7d32;"
                "background:#f4fbf4;color:#444;font-size:12px;'>"
                "<b>✓ Restored from the existing project.</b> The tables, diagnostics, and previews below "
                "were rebuilt from products already on disk; no ASP command was rerun."
                "</div>" + panel.value
            )

        # Do not force the user away from an earlier tab after completion.
        # Only a newly running step automatically opens.

    # --------------------------------------------------------
    # PRE-PROCESSING ACTION
    # --------------------------------------------------------
    def _on_pre_processing(self, _):
        # Clear the previous failure immediately when the user retries. The new
        # run should never remain visually marked as failed until it finishes.
        if not self._execution_busy():
            self.preprocess_summary.value = ""
            self.preprocess_summary_details.selected_index = None
            self.preprocess_progress.bar_style = ""
            self._reset_preprocess_stage_tabs(self._selected_preprocess_stages())
            controls = self._execution_control_groups.get("preprocessing")
            if controls:
                controls["status"].value = (
                    "<span style='color:#1565c0;font-size:12px;'>Starting new run; previous error cleared.</span>"
                )
        self._launch_background_execution(
            "preprocessing", "ASP pre-processing", self._run_pre_processing_task
        )

    def _run_pre_processing_task(self):
        self.preprocess_summary.value = ""
        self.preprocess_progress.bar_style = ""
        self.run_preprocessing.disabled = True

        selected_stages = self._selected_preprocess_stages()
        self._reset_preprocess_stage_tabs(selected_stages)

        try:
            run_all = self.preprocess_run_mode.value == "all"

            if run_all and not getattr(self, "_reference_dem_ready", False):
                raise ValueError(
                    "Prepare and inspect the reference DEMs first for a full "
                    "pre-processing run. To rerun an individual stage, switch "
                    "Run mode to 'Run one step'."
                )

            settings = self._build_settings()
            processing = self._build_pre_processing_settings(
                required_stages=selected_stages
            )

            selected_labels = [
                self._preprocess_stage_labels[stage]
                for stage in selected_stages
            ]
            self._set_preprocess_progress(
                1,
                (
                    "Starting complete ASP pre-processing"
                    if run_all
                    else f"Starting {selected_labels[0]}"
                ),
            )

            result = run_pre_processing(
                settings,
                processing,
                progress_callback=self._set_preprocess_progress,
                result_callback=self._update_preprocess_stage_result,
                stages=None if run_all else selected_stages,
            )

            self.last_pre_processing = result
            paths = result["paths"]

            output_lines = []
            selected_set = set(selected_stages)

            if "bundle_adjustment" in selected_set:
                output_lines.append(
                    "<b>Bundle-adjust prefix:</b> "
                    f"<code>{html.escape(str(paths['ba_prefix']))}</code>"
                )
            if "preliminary_stereo" in selected_set:
                output_lines.append(
                    "<b>Preliminary point cloud:</b> "
                    f"<code>{html.escape(str(paths['prelim_point_cloud']))}</code>"
                )
            if "preliminary_dem" in selected_set:
                output_lines.append(
                    "<b>Preliminary DSM:</b> "
                    f"<code>{html.escape(str(paths['prelim_dem']))}</code>"
                )
            if "lidar_alignment" in selected_set:
                output_lines.append(
                    "<b>Alignment transform:</b> "
                    f"<code>{html.escape(str(paths['align_transform']))}</code>"
                )
            if "camera_transform" in selected_set:
                output_lines.append(
                    "<b>Aligned camera prefix:</b> "
                    f"<code>{html.escape(str(paths['aligned_ba_prefix']))}</code>"
                )
            if "map_projection" in selected_set:
                map_lines = "<br>".join(
                    f"<code>{view}: {html.escape(str(paths['mapprojected'][view]))}</code>"
                    for view in settings.image_names
                )
                output_lines.append(
                    "<b>Map-projected images:</b><br>" + map_lines
                )

            mode_text = (
                "Complete six-stage chain"
                if run_all
                else "Single-stage rerun / experiment"
            )

            self.preprocess_summary_details.selected_index = None
            self.preprocess_summary.value = (
                "<div style='margin:10px 0;padding:10px;border-left:4px "
                "solid #2e7d32;background:#f4fbf4;'>"
                "<b>✓ Pre-processing completed.</b><br>"
                f"<b>Run mode:</b> {html.escape(mode_text)}<br>"
                "<b>Executed:</b> "
                + html.escape(" → ".join(selected_labels))
                + "<br>"
                f"<b>Camera model:</b> {html.escape(_camera_model_label(processing.camera_model))}<br>"
                f"<b>ASP session:</b> <code>-t {html.escape(paths['session_type'])}</code><br>"
                f"<b>Preliminary pair:</b> {html.escape(paths['pair'])}<br><br>"
                + "<br>".join(output_lines)
                + "<br><br><b>Detailed ASP logs:</b> "
                f"<code>{html.escape(str(paths['log_dir']))}</code><br>"
                "<b>Run summary log:</b> "
                f"<code>{html.escape(str(result['summary_log']))}</code>"
                "</div>"
            )

            for key in ("preliminary_plot", "map_plot"):
                payload = result.get(key)
                if payload is not None and payload.get("figure") is not None:
                    plt.close(payload["figure"])

            self.preprocess_progress.bar_style = "success"

        except WorkflowCancelled as exc:
            self.preprocess_progress.bar_style = "warning"
            try:
                settings = self._build_settings()
                log_dir = _processing_log_dir(settings)
            except Exception:
                log_dir = Path("(log path unavailable)")
            self.preprocess_summary_details.selected_index = 0
            self.preprocess_summary.value = (
                "<div style='margin:10px 0;padding:10px;border-left:4px "
                "solid #d28b00;background:#fffaf0;'>"
                "<b>■ Pre-processing stopped by user.</b><br>"
                f"{html.escape(str(exc))}<br><br>"
                "Completed upstream stages are preserved. A command interrupted mid-write "
                "may have partial output files; enable <b>Overwrite existing outputs</b> "
                "before rerunning that same step if ASP reports that its prefix already exists."
                "<br><b>Detailed ASP logs:</b> "
                f"<code>{html.escape(str(log_dir))}</code>"
                "</div>"
            )
            raise
        except Exception as exc:
            self.preprocess_progress.bar_style = "danger"

            try:
                settings = self._build_settings()
                log_dir = _processing_log_dir(settings)
            except Exception:
                log_dir = Path("(log path unavailable)")

            self.preprocess_summary_details.selected_index = 0
            self.preprocess_summary.value = (
                "<div style='margin:10px 0;padding:10px;border-left:4px "
                "solid #b00020;background:#fff4f4;'>"
                "<b>✗ Pre-processing stopped.</b><br>"
                f"{html.escape(type(exc).__name__ + ': ' + str(exc))}"
                "<br><br>Any stages completed before the error remain on disk. "
                "Switch to <b>Run one step</b> to rerun only the failed stage "
                "after correcting its settings."
                "<br><b>Detailed ASP logs:</b> "
                f"<code>{html.escape(str(log_dir))}</code>"
                "</div>"
            )

        finally:
            self._refresh_preprocess_run_button_state()

    # --------------------------------------------------------
    # POINT-CLOUD ACTION
    # --------------------------------------------------------
    def _on_point_cloud(self, _):
        if not self._execution_busy():
            self.point_cloud_summary.value = ""
            self.point_cloud_summary_details.selected_index = None
            self.point_cloud_results.children = ()
            self.point_cloud_progress.bar_style = ""
            controls = self._execution_control_groups.get("point_cloud")
            if controls:
                controls["status"].value = (
                    "<span style='color:#1565c0;font-size:12px;'>Starting new run; previous error cleared.</span>"
                )
        self._launch_background_execution(
            "point_cloud", "Point-cloud reconstruction", self._run_point_cloud_task
        )

    def _run_point_cloud_task(self):
        self.point_cloud_summary.value = ""
        self.point_cloud_results.children = ()
        self.point_cloud_progress.bar_style = ""
        self.run_point_cloud.disabled = True

        try:
            settings = self._build_settings()
            processing = (
                self._build_pre_processing_settings()
            )
            final = self._build_final_settings()

            self._runtime_begin("point_cloud", settings)
            result = run_point_cloud_reconstruction(
                settings,
                processing,
                final,
                progress_callback=(
                    self._set_point_cloud_progress
                ),
            )
            self._runtime_finish("point_cloud", settings)

            self.last_point_cloud = result

            self.point_cloud_results.children = (
                self.widgets.HTML(
                    self._dataframe_html(
                        result[
                            "products_df"
                        ]
                    )
                ),
            )

            self.point_cloud_summary_details.selected_index = None
            self.point_cloud_summary.value = (
                "<div style='margin:10px 0;"
                "padding:10px;border-left:4px "
                "solid #2e7d32;background:#f4fbf4;'>"
                "<b>✓ Point-cloud reconstruction completed.</b><br>"
                f"<b>Camera model:</b> {html.escape(_camera_model_label(processing.camera_model))}<br>"
                f"<b>ASP session:</b> <code>-t {html.escape(_camera_model_session(settings, processing))}</code><br>"
                f"<b>Mode:</b> "
                f"{html.escape(final.stereo_mode)}<br>"
                f"<b>Algorithm:</b> "
                f"{html.escape(final.algorithm_tag)}<br>"
                "<b>Saved product table:</b> "
                f"<code>{html.escape(str(result['products_csv']))}</code>"
                "<br><b>Detailed ASP logs:</b> "
                f"<code>{html.escape(str(_processing_log_dir(settings)))}</code>"
                "</div>"
            )

            self.point_cloud_progress.bar_style = (
                "success"
            )

            if self.auto_generate_dsm.value:
                self._pending_auto_final_dsm = True

        except WorkflowCancelled as exc:
            self.point_cloud_progress.bar_style = "warning"
            self.point_cloud_summary_details.selected_index = 0
            self.point_cloud_summary.value = (
                "<div style='margin:10px 0;padding:10px;border-left:4px "
                "solid #d28b00;background:#fffaf0;'>"
                "<b>■ Point-cloud reconstruction stopped by user.</b><br>"
                f"{html.escape(str(exc))}<br><br>"
                "Completed stereo configurations remain on disk. If the interrupted "
                "configuration left partial outputs, enable overwrite before rerunning it."
                "</div>"
            )
            self._pending_auto_final_dsm = False
            raise
        except Exception as exc:
            self.point_cloud_progress.bar_style = (
                "danger"
            )

            try:
                settings = self._build_settings()
                log_dir = (
                    _processing_log_dir(
                        settings
                    )
                )
            except Exception:
                log_dir = Path(
                    "(log path unavailable)"
                )

            self.point_cloud_summary_details.selected_index = 0
            self.point_cloud_summary.value = (
                "<div style='margin:10px 0;"
                "padding:10px;border-left:4px "
                "solid #b00020;background:#fff4f4;'>"
                "<b>✗ Point-cloud reconstruction stopped.</b><br>"
                f"{html.escape(type(exc).__name__ + ': ' + str(exc))}"
                "<br><br><b>Detailed ASP logs:</b> "
                f"<code>{html.escape(str(log_dir))}</code>"
                "</div>"
            )

        finally:
            self.run_point_cloud.disabled = self._execution_busy()

    # --------------------------------------------------------
    # OPTIONAL CO-REGISTRATION ACTION
    # --------------------------------------------------------
    def _on_open_coregistration_notebook(self, _):
        """Create a handoff file and open the bundled xDEM notebook in a new tab."""
        from IPython.display import Javascript, display

        try:
            settings = self._build_settings()
            template = (
                Path(__file__).resolve().parent
                / "coregistration"
                / "Coregistration_Process-Final-withPlannimetric.ipynb"
            )
            if not template.is_file():
                raise FileNotFoundError(f"Co-registration notebook not found:\n{template}")

            final_csv = settings.metadata_dir / "final_dsm_products.csv"
            final_dsms = []
            if final_csv.is_file():
                try:
                    df = pd.read_csv(final_csv)
                    if "DSM" in df.columns:
                        final_dsms = [str(v) for v in df["DSM"].dropna().tolist()]
                except Exception:
                    final_dsms = []

            handoff = settings.project_dir / "coregistration_handoff.json"
            handoff.write_text(
                json.dumps(
                    {
                        "project_name": settings.project_name,
                        "project_dir": str(settings.project_dir),
                        "final_dsm_products_csv": str(final_csv) if final_csv.is_file() else "",
                        "final_dsms": final_dsms,
                        "alignment_reference_dem": self.alignment_dem.value.strip(),
                        "map_projection_dem": self.mapproject_dem.value.strip(),
                        "note": (
                            "Optional xDEM co-registration is outside ASP. Configure the stable-area "
                            "and reference-DEM inputs in the co-registration notebook before running it."
                        ),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            self.coregistration_status.value = (
                "<div style='margin:7px 0;padding:9px 11px;border-left:4px solid #6a3d9a;"
                "background:#f7f2fb;color:#444;font-size:12px;line-height:1.5;'>"
                "<b>✓ Co-registration handoff prepared.</b><br>"
                f"<b>Notebook:</b> <code>{html.escape(str(template))}</code><br>"
                f"<b>Project handoff:</b> <code>{html.escape(str(handoff))}</code><br>"
                "The notebook opens separately because co-registration is an optional post-processing "
                "step outside ASP. Review its INPUTS cell before execution."
                "</div>"
            )

            # Build the browser URL from the directory of the notebook that is
            # currently open, not from Python's process working directory.  A
            # Jupyter server can be rooted above the workflow folder (for
            # example, /home/user while the workflow lives in
            # /home/user/Final Software).  Using Path.cwd() in that situation
            # drops the workflow-folder component and produces a 404.
            notebook_name = template.name
            script = f"""
            (function() {{
                const notebookName = {json.dumps(template.name)};
                const path = window.location.pathname;
                let url = null;

                function encodedChildPath(name) {{
                    return 'coregistration/' + encodeURIComponent(name);
                }}

                if (path.includes('/lab/tree/')) {{
                    const currentDir = path.substring(0, path.lastIndexOf('/') + 1);
                    url = currentDir + encodedChildPath(notebookName);
                }} else if (path.includes('/notebooks/')) {{
                    const currentDir = path.substring(0, path.lastIndexOf('/') + 1);
                    url = currentDir + encodedChildPath(notebookName);
                }} else {{
                    // Fallback for unusual frontends: resolve relative to the
                    // current page directory.
                    url = new URL(encodedChildPath(notebookName), window.location.href).href;
                }}

                window.open(url, '_blank');
            }})();
            """
            with self.coregistration_output:
                display(Javascript(script))

        except Exception as exc:
            self.coregistration_status.value = (
                "<div style='margin:7px 0;padding:9px 11px;border-left:4px solid #b00020;"
                "background:#fff4f4;color:#444;font-size:12px;'>"
                "<b>✗ Co-registration notebook could not be opened.</b><br>"
                + html.escape(type(exc).__name__ + ": " + str(exc)) +
                "</div>"
            )

    # --------------------------------------------------------
    # FINAL DSM ACTION
    # --------------------------------------------------------
    def _on_final_dsm(self, _):
        if not self._execution_busy():
            self.final_dsm_summary.value = ""
            self.final_dsm_summary_details.selected_index = None
            self.final_dsm_results.children = ()
            self.final_dsm_progress.bar_style = ""
            controls = self._execution_control_groups.get("final_dsm")
            if controls:
                controls["status"].value = (
                    "<span style='color:#1565c0;font-size:12px;'>Starting new run; previous error cleared.</span>"
                )
        self._launch_background_execution(
            "final_dsm", "Final DSM generation", self._run_final_dsm_task
        )

    def _run_final_dsm_task(self):
        self.final_dsm_summary.value = ""
        self.final_dsm_results.children = ()
        self.final_dsm_progress.bar_style = ""
        self.run_final_dsm.disabled = True

        try:
            settings = self._build_settings()
            processing = (
                self._build_pre_processing_settings()
            )
            final = self._build_final_settings()

            self._runtime_begin("final_dsm", settings)
            result = generate_final_dsms(
                settings,
                processing,
                final,
                progress_callback=(
                    self._set_final_dsm_progress
                ),
            )
            self._runtime_finish("final_dsm", settings)

            self.last_final_dsm = result

            table_tab = self.widgets.HTML(
                self._dataframe_html(
                    result["products_df"]
                )
            )

            children = [table_tab]
            titles = ["Generated DSMs"]

            for product in result[
                "products"
            ]:
                plot = product.get("plot") or {}
                png_path = Path(plot.get("png", ""))
                image_html = ""

                if png_path.is_file():
                    encoded = base64.b64encode(
                        png_path.read_bytes()
                    ).decode("ascii")
                    image_html = (
                        "<div style='padding:10px;'>"
                        "<div style='font-size:14px;font-weight:700;margin-bottom:8px;'>"
                        "Final DSM + intersection error</div>"
                        f"<img src='data:image/png;base64,{encoded}' "
                        "style='max-width:100%;height:auto;"
                        "border:1px solid #d7dde5;border-radius:2px;'/>"
                        "<div style='margin-top:9px;font-size:12px;line-height:1.5;'>"
                        f"<b>Final DSM:</b> <code>{html.escape(str(product.get('dem', '')))}</code><br>"
                        f"<b>Intersection error:</b> <code>{html.escape(str(product.get('error_image', '') or 'Not generated'))}</code><br>"
                        f"<b>Saved PNG:</b> <code>{html.escape(str(plot.get('png', '')))}</code><br>"
                        f"<b>Saved PDF:</b> <code>{html.escape(str(plot.get('pdf', '')))}</code><br>"
                        f"<b>Detailed log:</b> <code>{html.escape(str(product.get('log', '')))}</code>"
                        "</div></div>"
                    )
                else:
                    image_html = (
                        "<div style='padding:10px;color:#8a5a00;'>"
                        "The final DSM was generated, but the QC preview PNG could not be found.<br>"
                        f"<b>Final DSM:</b> <code>{html.escape(str(product.get('dem', '')))}</code><br>"
                        f"<b>Intersection error:</b> <code>{html.escape(str(product.get('error_image', '') or 'Not generated'))}</code>"
                        "</div>"
                    )

                children.append(
                    self.widgets.HTML(
                        value=image_html,
                        layout=self.widgets.Layout(width="100%"),
                    )
                )
                titles.append(
                    product["product_tag"]
                )

            tabs = self.widgets.Tab(
                children=children
            )

            for index, title in enumerate(
                titles
            ):
                tabs.set_title(
                    index,
                    title,
                )

            self.final_dsm_results.children = (
                tabs,
            )

            self.final_dsm_summary_details.selected_index = None
            self.final_dsm_summary.value = (
                "<div style='margin:10px 0;"
                "padding:10px;border-left:4px "
                "solid #2e7d32;background:#f4fbf4;'>"
                "<b>✓ Final DSM generation completed.</b><br>"
                f"<b>DSM resolution:</b> "
                f"{final.final_dsm_resolution_m} m<br>"
                "<b>Triangulation-error limit:</b> "
                f"{final.max_valid_triangulation_error_m} m<br>"
                "<b>Saved product table:</b> "
                f"<code>{html.escape(str(result['products_csv']))}</code>"
                "<br><b>Figures:</b> "
                f"<code>{html.escape(str(settings.figure_dir))}</code>"
                "<br><b>Detailed ASP logs:</b> "
                f"<code>{html.escape(str(_processing_log_dir(settings)))}</code>"
                "</div>"
            )

            for product in result[
                "products"
            ]:
                plt.close(
                    product["plot"][
                        "figure"
                    ]
                )

            self.final_dsm_progress.bar_style = (
                "success"
            )

        except WorkflowCancelled as exc:
            self.final_dsm_progress.bar_style = "warning"
            self.final_dsm_summary_details.selected_index = 0
            self.final_dsm_summary.value = (
                "<div style='margin:10px 0;padding:10px;border-left:4px "
                "solid #d28b00;background:#fffaf0;'>"
                "<b>■ Final DSM generation stopped by user.</b><br>"
                f"{html.escape(str(exc))}<br><br>"
                "Already completed DSM products are preserved. Rerun after enabling overwrite "
                "if the interrupted point2dem output is incomplete."
                "</div>"
            )
            raise
        except Exception as exc:
            self.final_dsm_progress.bar_style = (
                "danger"
            )

            try:
                settings = self._build_settings()
                log_dir = (
                    _processing_log_dir(
                        settings
                    )
                )
            except Exception:
                log_dir = Path(
                    "(log path unavailable)"
                )

            self.final_dsm_summary_details.selected_index = 0
            self.final_dsm_summary.value = (
                "<div style='margin:10px 0;"
                "padding:10px;border-left:4px "
                "solid #b00020;background:#fff4f4;'>"
                "<b>✗ Final DSM generation stopped.</b><br>"
                f"{html.escape(type(exc).__name__ + ': ' + str(exc))}"
                "<br><br><b>Detailed ASP logs:</b> "
                f"<code>{html.escape(str(log_dir))}</code>"
                "</div>"
            )

        finally:
            self.run_final_dsm.disabled = self._execution_busy()


# Override only the entry point so the original v0.5 interface becomes
# the base of the extended full workflow.
def project_setup():
    return FullProjectSetupUI().display()
