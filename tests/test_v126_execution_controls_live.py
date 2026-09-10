from pathlib import Path
import os
import threading
import time

import asp_utils2


def _wait_for(predicate, timeout=5.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_v126_live_pause_resume_stop_freezes_real_process_group(tmp_path):
    if not asp_utils2.ManagedProcessController().pause_supported:
        return

    controller = asp_utils2.ManagedProcessController()
    controller.begin("live-control-test")
    log = tmp_path / "command.log"
    ticks = tmp_path / "ticks.txt"
    child_pid_file = tmp_path / "child.pid"
    result = {}

    # Parent and child both append to the same file. A real process-group pause
    # must freeze both, and Stop must return the worker promptly.
    script = (
        f"(while true; do echo child >> '{ticks}'; sleep 0.08; done) & "
        f"child=$!; echo $child > '{child_pid_file}'; "
        f"while true; do echo parent >> '{ticks}'; sleep 0.08; done"
    )

    def worker():
        asp_utils2._set_current_process_controller(controller)
        try:
            try:
                asp_utils2._run_asp_command("bash", ["-lc", script], log, tmp_path)
                result["status"] = "completed"
            except asp_utils2.WorkflowCancelled:
                result["status"] = "cancelled"
        finally:
            asp_utils2._clear_current_process_controller()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    assert _wait_for(lambda: controller.has_active_process)
    assert _wait_for(lambda: ticks.exists() and ticks.stat().st_size > 0)
    assert _wait_for(child_pid_file.exists)

    assert controller.pause()
    assert controller.state == "paused"
    assert "Pause signal delivered" in controller.last_control_message

    # Allow writes already buffered/in flight to settle, then verify the file is frozen.
    time.sleep(0.2)
    size1 = ticks.stat().st_size
    time.sleep(0.35)
    size2 = ticks.stat().st_size
    assert size2 == size1

    assert controller.resume()
    assert controller.state == "running"
    assert "Resume signal delivered" in controller.last_control_message
    assert _wait_for(lambda: ticks.stat().st_size > size2, timeout=2.0)

    stop_started = time.time()
    assert controller.stop()
    # Stop must return control to the ipywidget callback immediately; the
    # termination grace period runs in a separate daemon thread.
    assert time.time() - stop_started < 0.25
    assert controller.state == "stopping"
    assert _wait_for(lambda: not thread.is_alive(), timeout=5.0)
    assert result["status"] == "cancelled"

    child_pid = int(child_pid_file.read_text().strip())
    # The child may briefly be a zombie while reaped, but it must not remain an
    # actively running worker. psutil gives a portable check when available.
    if asp_utils2.psutil is not None:
        try:
            child = asp_utils2.psutil.Process(child_pid)
            assert (not child.is_running()) or child.status() == asp_utils2.psutil.STATUS_ZOMBIE
        except asp_utils2.psutil.NoSuchProcess:
            pass


def test_v126_wrapper_detection_and_control_diagnostics(tmp_path):
    wrapper = tmp_path / "bundle_adjust"
    wrapper.write_text(
        "#!/usr/bin/python\nfrom pleiades_asp_runner.cli import main\nmain()\n",
        encoding="utf-8",
    )
    assert asp_utils2._looks_like_workflow_cli_wrapper(wrapper)

    ordinary = tmp_path / "ordinary"
    ordinary.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    assert not asp_utils2._looks_like_workflow_cli_wrapper(ordinary)

    controller = asp_utils2.ManagedProcessController()
    assert controller.active_pid is None
    assert controller.last_control_message == "Ready."
