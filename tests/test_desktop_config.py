"""Tests for Desktop session configuration and URL protection keys (TASK-22)."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import SecretStr, ValidationError

from rmr.desktop_config import (
    DesktopSettings,
    build_mcp_path,
    build_public_url,
    generate_url_key,
    load_settings,
    normalize_domain,
    save_settings,
    validate_project_root,
)


class TestDesktopConfig(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.temp_path = Path(self.temp_dir.name).resolve()
        self.repo_root = self._init_git_repo(self.temp_path / "project")
        (self.repo_root / "src").mkdir()

    def _init_git_repo(self, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
        return path

    # --- Domain normalization & validation ---

    def test_normalize_domain_valid_hostnames(self):
        self.assertEqual(
            normalize_domain("clean-swift-mule.ngrok-free.app"),
            "clean-swift-mule.ngrok-free.app",
        )
        self.assertEqual(normalize_domain("localhost"), "localhost")
        self.assertEqual(normalize_domain("127.0.0.1"), "127.0.0.1")
        self.assertEqual(normalize_domain("example.com."), "example.com")

    def test_normalize_domain_valid_https_urls(self):
        self.assertEqual(
            normalize_domain("https://clean-swift-mule.ngrok-free.app"),
            "clean-swift-mule.ngrok-free.app",
        )
        self.assertEqual(
            normalize_domain("https://clean-swift-mule.ngrok-free.app/"),
            "clean-swift-mule.ngrok-free.app",
        )
        self.assertEqual(
            normalize_domain("  https://my-domain.org/  "),
            "my-domain.org",
        )

    def test_normalize_domain_rejects_non_https_scheme(self):
        with self.assertRaises(ValueError) as ctx:
            normalize_domain("http://clean-swift-mule.ngrok-free.app")
        self.assertIn("Only HTTPS URLs", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            normalize_domain("ftp://example.com")
        self.assertIn("Only HTTPS URLs", str(ctx.exception))

    def test_normalize_domain_rejects_credentials(self):
        with self.assertRaises(ValueError) as ctx:
            normalize_domain("https://user:pass@example.com")
        self.assertIn("credentials", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            normalize_domain("user@example.com")
        self.assertIn("credentials", str(ctx.exception))

    def test_normalize_domain_rejects_port(self):
        with self.assertRaises(ValueError) as ctx:
            normalize_domain("example.com:8000")
        self.assertIn("port", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            normalize_domain("https://example.com:8000")
        self.assertIn("port", str(ctx.exception))

    def test_normalize_domain_rejects_paths(self):
        with self.assertRaises(ValueError) as ctx:
            normalize_domain("example.com/mcp")
        self.assertIn("path", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            normalize_domain("https://example.com/mcp")
        self.assertIn("path", str(ctx.exception))

    def test_normalize_domain_rejects_query_and_fragment(self):
        with self.assertRaises(ValueError) as ctx:
            normalize_domain("example.com?query=1")
        self.assertIn("query", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            normalize_domain("example.com#fragment")
        self.assertIn("fragment", str(ctx.exception))

    def test_normalize_domain_rejects_empty_url_components(self):
        for suffix, message in ((":", "port"), ("?", "query"), ("#", "fragment")):
            with (
                self.subTest(suffix=suffix),
                self.assertRaisesRegex(ValueError, message),
            ):
                normalize_domain(f"https://example.com{suffix}")
        with self.assertRaisesRegex(ValueError, "credentials"):
            normalize_domain("https://@example.com")

    def test_normalize_domain_rejects_empty_or_malformed(self):
        with self.assertRaises(ValueError):
            normalize_domain("")
        with self.assertRaises(ValueError):
            normalize_domain("   ")
        with self.assertRaises(ValueError):
            normalize_domain("invalid domain with spaces.com")
        with self.assertRaises(ValueError):
            normalize_domain("https://exam\nple.com")

    # --- validate_project_root ---

    def test_validate_project_root_success(self):
        validated = validate_project_root(self.repo_root)
        self.assertEqual(validated, self.repo_root)

        # Freshly initialized git repo in temp dir
        repo = self._init_git_repo(self.temp_path / "repo1")
        (repo / "file.txt").write_text("hello", encoding="utf-8")
        self.assertEqual(validate_project_root(repo), repo)

    def test_validate_project_root_non_existent(self):
        with self.assertRaises(ValueError) as ctx:
            validate_project_root(self.temp_path / "non_existent_dir_999")
        self.assertIn("does not exist", str(ctx.exception))

    def test_validate_project_root_not_a_directory(self):
        file_path = self.temp_path / "test_file.txt"
        file_path.write_text("hello", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            validate_project_root(file_path)
        self.assertIn("not a directory", str(ctx.exception))

    def test_validate_project_root_outside_git(self):
        # Temp dir without git init
        non_git_dir = self.temp_path / "plain_dir"
        non_git_dir.mkdir()
        with self.assertRaises(ValueError) as ctx:
            validate_project_root(non_git_dir)
        self.assertIn("not inside a Git repository", str(ctx.exception))

    def test_validate_project_root_subdirectory_rejected(self):
        subdir = self.repo_root / "src"
        with self.assertRaises(ValueError) as ctx:
            validate_project_root(subdir)
        self.assertIn("subdirectory", str(ctx.exception))
        self.assertIn("Please select the repository root", str(ctx.exception))

    # --- generate_url_key & format ---

    def test_generate_url_key(self):
        key1 = generate_url_key()
        key2 = generate_url_key()
        self.assertIsInstance(key1, str)
        self.assertGreaterEqual(len(key1), 32)
        self.assertNotEqual(key1, key2)
        # Check URL safe characters
        self.assertTrue(all(c.isalnum() or c in ("-", "_") for c in key1))

    # --- build_mcp_path & build_public_url ---

    def test_build_mcp_path(self):
        key = SecretStr("a" * 32)
        self.assertEqual(build_mcp_path(key), f"/mcp/{'a' * 32}")
        self.assertEqual(build_mcp_path("b" * 32), f"/mcp/{'b' * 32}")

        with self.assertRaises(ValueError):
            build_mcp_path(SecretStr(""))

    def test_build_mcp_path_rejects_invalid_segments(self):
        key = "a" * 32
        for invalid_key in (f"/{key}", f"{key}/extra", f"{key}\n", f"{key}?"):
            with self.subTest(invalid_key=invalid_key), self.assertRaises(ValueError):
                build_mcp_path(invalid_key)

    def test_build_public_url(self):
        settings = DesktopSettings(
            project_root=self.repo_root,
            domain="clean-swift-mule.ngrok-free.app",
            port=8000,
            url_key=SecretStr("secure_random_key_of_32_characters_minimum"),
        )
        expected_url = "https://clean-swift-mule.ngrok-free.app/mcp/secure_random_key_of_32_characters_minimum"
        self.assertEqual(build_public_url(settings), expected_url)

    # --- DesktopSettings Model Validation ---

    def test_desktop_settings_valid(self):
        key = SecretStr("x" * 32)
        settings = DesktopSettings(
            project_root=self.repo_root,
            domain="https://my-tunnel.ngrok-free.app/",
            port=8080,
            url_key=key,
        )
        self.assertEqual(settings.domain, "my-tunnel.ngrok-free.app")
        self.assertEqual(settings.port, 8080)
        self.assertEqual(settings.url_key.get_secret_value(), "x" * 32)
        self.assertEqual(settings.project_root, self.repo_root)

    def test_desktop_settings_default_port(self):
        settings = DesktopSettings(
            project_root=self.repo_root,
            domain="example.com",
            url_key=SecretStr("y" * 32),
        )
        self.assertEqual(settings.port, 8000)

    def test_desktop_settings_port_range(self):
        # Valid boundaries
        DesktopSettings(
            project_root=self.repo_root,
            domain="example.com",
            port=1,
            url_key=SecretStr("y" * 32),
        )
        DesktopSettings(
            project_root=self.repo_root,
            domain="example.com",
            port=65535,
            url_key=SecretStr("y" * 32),
        )

        # Invalid ports
        for invalid_port in [0, -1, 65536, 100000, True, 8000.5]:
            with self.assertRaises(ValidationError):
                DesktopSettings(
                    project_root=self.repo_root,
                    domain="example.com",
                    port=invalid_port,
                    url_key=SecretStr("y" * 32),
                )

    def test_desktop_settings_url_key_too_short(self):
        with self.assertRaises(ValidationError) as ctx:
            DesktopSettings(
                project_root=self.repo_root,
                domain="example.com",
                url_key=SecretStr("short_key_under_32"),
            )
        self.assertIn("at least 32 characters long", str(ctx.exception))

    def test_desktop_settings_url_key_invalid_chars(self):
        invalid_keys = [
            "this_is_32_characters_long_with_spaces    ",
            "this_is_32_characters_long_with_slash/123",
            "this_is_32_characters_long_with_at_sign@1",
            "this_is_32_characters_long_with_qmark?123",
            "x" * 32 + "\n",
        ]
        for key in invalid_keys:
            with self.assertRaises(ValidationError) as ctx:
                DesktopSettings(
                    project_root=self.repo_root,
                    domain="example.com",
                    url_key=SecretStr(key),
                )
            self.assertIn("URL-safe characters", str(ctx.exception))

    def test_desktop_settings_hides_invalid_plaintext_key(self):
        raw_key = "sample_private_key_with_bad_char*"
        with self.assertRaisesRegex(ValidationError, "URL-safe characters") as error:
            DesktopSettings(
                project_root=self.repo_root,
                domain="example.com",
                url_key=raw_key,
            )
        self.assertNotIn(raw_key, str(error.exception))

    def test_desktop_settings_rejects_subdirectory_project_root(self):
        with self.assertRaises(ValidationError):
            DesktopSettings(
                project_root=self.repo_root / "src",
                domain="example.com",
                url_key=SecretStr("z" * 32),
            )

    def test_desktop_settings_rejects_outside_git_project_root(self):
        non_git = self.temp_path / "no_git"
        non_git.mkdir()
        with self.assertRaises(ValidationError):
            DesktopSettings(
                project_root=non_git,
                domain="example.com",
                url_key=SecretStr("z" * 32),
            )

    # --- save_settings & load_settings ---

    def test_save_and_load_settings_roundtrip(self):
        config_file = self.temp_path / "desktop" / "session_config.json"
        raw_key = generate_url_key()
        settings = DesktopSettings(
            project_root=self.repo_root,
            domain="https://clean-swift-mule.ngrok-free.app",
            port=9000,
            url_key=SecretStr(raw_key),
        )

        save_settings(config_file, settings)
        self.assertTrue(config_file.is_file())

        loaded = load_settings(config_file)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.project_root, settings.project_root)
        self.assertEqual(loaded.domain, "clean-swift-mule.ngrok-free.app")
        self.assertEqual(loaded.port, 9000)
        self.assertEqual(loaded.url_key.get_secret_value(), raw_key)
        self.assertEqual(build_public_url(loaded), build_public_url(settings))

    def test_failed_replace_keeps_existing_settings(self):
        config_file = self.temp_path / "settings.json"
        settings = DesktopSettings(
            project_root=self.repo_root,
            domain="example.com",
            url_key=generate_url_key(),
        )
        save_settings(config_file, settings)
        original = config_file.read_bytes()

        with (
            patch(
                "rmr.desktop_config.os.replace", side_effect=OSError("replace failed")
            ),
            self.assertRaises(OSError),
        ):
            save_settings(config_file, settings.model_copy(update={"port": 9000}))

        self.assertEqual(config_file.read_bytes(), original)
        self.assertFalse(list(config_file.parent.glob("*.tmp")))

    def test_load_settings_missing_file_returns_none(self):
        missing_file = self.temp_path / "does_not_exist.json"
        self.assertIsNone(load_settings(missing_file))

    def test_load_settings_corrupted_json_returns_none(self):
        corrupt_file = self.temp_path / "corrupt.json"
        corrupt_file.write_text("{invalid_json: 123", encoding="utf-8")
        self.assertIsNone(load_settings(corrupt_file))

    def test_load_settings_rejects_invalid_stored_port_or_key(self):
        config_file = self.temp_path / "settings.json"
        valid = {
            "project_root": str(self.repo_root),
            "domain": "example.com",
            "port": 8000,
            "url_key": "x" * 32,
        }
        for change in ({"port": True}, {"port": 8000.5}, {"url_key": 10**31}):
            with self.subTest(change=change):
                config_file.write_text(json.dumps(valid | change), encoding="utf-8")
                self.assertIsNone(load_settings(config_file))

    def test_load_settings_invalid_content_returns_none_and_does_not_alter_file(self):
        invalid_file = self.temp_path / "invalid_data.json"
        original_content = json.dumps(
            {
                "project_root": str(self.repo_root / "src"),  # Invalid: subdirectory
                "domain": "example.com",
                "port": 8000,
                "url_key": "x" * 32,
            }
        )
        invalid_file.write_text(original_content, encoding="utf-8")

        result = load_settings(invalid_file)
        self.assertIsNone(result)

        # Ensure original file is completely intact, key was not regenerated or changed
        self.assertEqual(invalid_file.read_text(encoding="utf-8"), original_content)


if __name__ == "__main__":
    unittest.main()
