from __future__ import annotations

from pathlib import Path
import socket
import tempfile
import unittest

from src.configuration import global_env_path, load_home_config, onboard_robot_instance, resolve_robot_instance, save_secret


class ConfigurationTests(unittest.TestCase):
    def test_resolve_robot_instance_does_not_bootstrap_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            instance = resolve_robot_instance(
                robot_id="dreamgirl",
                robot_name="Dreamgirl",
                home_override=tmpdir,
            )

            self.assertTrue(instance.paths.home_config_path.exists())
            self.assertFalse((instance.paths.firmware_dir / "AGENTS.md").exists())
            self.assertFalse((instance.paths.memory_dir / "MEMORY.md").exists())
            self.assertFalse(instance.onboarded)
            self.assertFalse(instance.paths.robot_config_path.exists())

            cfg = load_home_config(tmpdir)
            self.assertEqual([], cfg.get("robots", []))

    def test_onboard_robot_instance_creates_workspace_and_secret_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            instance = onboard_robot_instance(
                robot_id="dreamgirl",
                robot_name="Dreamgirl",
                home_override=tmpdir,
                access_token="chat-secret",
                admin_token="admin-secret",
            )

            self.assertTrue(instance.paths.home_config_path.exists())
            self.assertTrue(instance.paths.robot_config_path.exists())
            self.assertTrue((instance.paths.firmware_dir / "AGENTS.md").exists())
            self.assertTrue((instance.paths.memory_dir / "MEMORY.md").exists())
            self.assertTrue((instance.paths.memory_dir / "DIRECTORY.md").exists())
            self.assertTrue((instance.paths.memory_dir / "CORE_MEMORIES.md").exists())
            self.assertTrue(instance.paths.logs_dir.exists())
            self.assertTrue(instance.paths.sessions_dir.exists())
            self.assertTrue(instance.paths.instance_env_path.exists())
            self.assertIn(instance.access_token_env, instance.paths.instance_env_path.read_text(encoding="utf-8"))
            self.assertIn(instance.admin_token_env, instance.paths.instance_env_path.read_text(encoding="utf-8"))

            cfg = load_home_config(tmpdir)
            robots = cfg.get("robots", [])
            self.assertEqual("dreamgirl", robots[0]["id"])
            self.assertNotIn("websocket", robots[0])
            self.assertEqual(str(instance.paths.workspace_dir), robots[0]["workspace"])
            self.assertEqual(str(instance.paths.robot_config_path), robots[0]["config_path"])

    def test_resolve_robot_instance_chooses_next_free_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", 0))
                occupied_port = sock.getsockname()[1]

                instance = resolve_robot_instance(
                    robot_id="busy-port-robot",
                    home_override=tmpdir,
                    bind_override="127.0.0.1",
                    port_override=occupied_port,
                )

                self.assertNotEqual(occupied_port, instance.port)
                self.assertGreater(instance.port, 0)

    def test_global_env_is_not_used_as_runtime_secret_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            global_secret_file = global_env_path(tmpdir)
            save_secret("CEPHIX_DREAMGIRL_WS_ACCESS_TOKEN", "central-secret", global_secret_file)

            onboard_robot_instance(
                robot_id="dreamgirl",
                robot_name="Dreamgirl",
                home_override=tmpdir,
            )

            instance = resolve_robot_instance(
                robot_id="dreamgirl",
                robot_name="Dreamgirl",
                home_override=tmpdir,
            )

            self.assertEqual("", instance.access_token)


if __name__ == "__main__":
    unittest.main()
