from pathlib import Path
import threading
import time

import asp_utils2


def test_v123_version():
    assert asp_utils2.DEV_VERSION == "1.5.4"


def test_managed_process_pause_resume_stop(tmp_path):
    controller = asp_utils2.ManagedProcessController()
    controller.begin("execution-control-test")
    log = tmp_path / "sleep.log"
    result = {}

    def worker():
        asp_utils2._set_current_process_controller(controller)
        try:
            try:
                asp_utils2._run_asp_command(
                    "bash",
                    ["-lc", "sleep 30"],
                    log,
                    tmp_path,
                )
                result["status"] = "completed"
            except asp_utils2.WorkflowCancelled:
                result["status"] = "cancelled"
        finally:
            asp_utils2._clear_current_process_controller()

    thread = threading.Thread(target=worker, daemon=True)
    started = time.time()
    thread.start()

    for _ in range(100):
        if controller.has_active_process:
            break
        time.sleep(0.02)

    assert controller.has_active_process

    if controller.pause_supported:
        assert controller.pause()
        assert controller.state == "paused"
        time.sleep(0.05)
        assert controller.resume()
        assert controller.state == "running"

    assert controller.stop()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert result["status"] == "cancelled"
    assert time.time() - started < 5
    assert "Run stopped by user" in log.read_text(errors="replace")


def test_ui_source_has_execution_controls_and_background_worker():
    source = Path(asp_utils2.__file__).read_text()
    assert 'description="Pause"' in source
    assert 'description="Resume"' in source
    assert 'description="Stop"' in source
    assert "_launch_background_execution" in source
    assert "threading.Thread" in source
    assert "start_new_session" in source
    assert "SIGSTOP" in source
    assert "SIGCONT" in source
    assert "_terminate_process_tree" in source
    assert '"camera_test", self.run_camera_test' in source
    assert '"preprocessing", self.run_preprocessing' in source
    assert '"point_cloud", self.run_point_cloud' in source
    assert '"final_dsm", self.run_final_dsm' in source
