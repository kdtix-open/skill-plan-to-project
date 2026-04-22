"""Tests for scripts/mint_app_token.py (FR #49)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import mint_app_token


def _generate_test_keypair(tmp_path: Path) -> Path:
    """Write a throwaway RSA key so PyJWT's sign + verify round-trips work."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path = tmp_path / "test_key.pem"
    path.write_bytes(pem)
    return path


class TestLoadConfig:
    def test_load_config_from_env(self, tmp_path, monkeypatch):
        key = _generate_test_keypair(tmp_path)
        monkeypatch.setenv("SDLCA_APP_ID", "12345")
        monkeypatch.setenv("SDLCA_APP_PRIVATE_KEY_PATH", str(key))
        # Avoid finding a user ~/.sdlca/app.conf during tests
        monkeypatch.setattr(mint_app_token.Path, "home", lambda: tmp_path)

        app_id, path = mint_app_token._load_config()
        assert app_id == "12345"
        assert path == key

    def test_load_config_from_conf_file(self, tmp_path, monkeypatch):
        key = _generate_test_keypair(tmp_path)
        (tmp_path / ".sdlca").mkdir()
        conf = tmp_path / ".sdlca" / "app.conf"
        conf.write_text(
            f'SDLCA_APP_ID="99999"\nSDLCA_APP_PRIVATE_KEY_PATH="{key}"\n'
        )
        monkeypatch.delenv("SDLCA_APP_ID", raising=False)
        monkeypatch.delenv("SDLCA_APP_PRIVATE_KEY_PATH", raising=False)
        monkeypatch.setattr(mint_app_token.Path, "home", lambda: tmp_path)

        app_id, path = mint_app_token._load_config()
        assert app_id == "99999"
        assert path == key

    def test_load_config_missing_app_id_exits(self, tmp_path, monkeypatch, capsys):
        monkeypatch.delenv("SDLCA_APP_ID", raising=False)
        monkeypatch.setattr(mint_app_token.Path, "home", lambda: tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            mint_app_token._load_config()
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "SDLCA_APP_ID not set" in captured.err

    def test_load_config_missing_key_path_exits(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("SDLCA_APP_ID", "12345")
        monkeypatch.delenv("SDLCA_APP_PRIVATE_KEY_PATH", raising=False)
        monkeypatch.setattr(mint_app_token.Path, "home", lambda: tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            mint_app_token._load_config()
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "SDLCA_APP_PRIVATE_KEY_PATH not set" in captured.err

    def test_load_config_key_path_not_found_exits(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("SDLCA_APP_ID", "12345")
        monkeypatch.setenv(
            "SDLCA_APP_PRIVATE_KEY_PATH", str(tmp_path / "does-not-exist.pem")
        )
        monkeypatch.setattr(mint_app_token.Path, "home", lambda: tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            mint_app_token._load_config()
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "private key not found" in captured.err


class TestSignAppJwt:
    def test_sign_produces_valid_rs256_jwt(self, tmp_path):
        import jwt as pyjwt
        from cryptography.hazmat.primitives import serialization

        key_path = _generate_test_keypair(tmp_path)
        token = mint_app_token._sign_app_jwt("12345", key_path)

        # Verify with the corresponding public key
        priv = serialization.load_pem_private_key(
            key_path.read_bytes(), password=None
        )
        pub = priv.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        decoded = pyjwt.decode(token, pub, algorithms=["RS256"])
        assert decoded["iss"] == "12345"
        assert "iat" in decoded
        assert "exp" in decoded
        # exp > iat by roughly 10 minutes
        assert decoded["exp"] - decoded["iat"] >= 540


class TestMintForOrg:
    def test_mint_auto_discovers_installation_and_mints(
        self, tmp_path, monkeypatch
    ):
        key = _generate_test_keypair(tmp_path)
        monkeypatch.setenv("SDLCA_APP_ID", "12345")
        monkeypatch.setenv("SDLCA_APP_PRIVATE_KEY_PATH", str(key))
        monkeypatch.setattr(mint_app_token.Path, "home", lambda: tmp_path)
        monkeypatch.delenv("SDLCA_APP_INSTALLATION_ID_KDTIX_OPEN", raising=False)

        def fake_http(url, token, method="GET"):
            if url.endswith("/orgs/kdtix-open/installation"):
                assert method == "GET"
                return 200, {"id": 9876543}
            if url.endswith("/app/installations/9876543/access_tokens"):
                assert method == "POST"
                return 201, {
                    "token": "ghs_TESTOKEN",
                    "expires_at": "2026-04-22T08:00:00Z",
                }
            raise AssertionError(f"unexpected URL: {url}")

        with patch.object(mint_app_token, "_http_request", side_effect=fake_http):
            result = mint_app_token.mint_for_org("kdtix-open")

        assert result["token"] == "ghs_TESTOKEN"  # noqa: S105 — test fixture
        assert result["expires_at"] == "2026-04-22T08:00:00Z"
        assert result["installation_id"] == 9876543

    def test_mint_respects_explicit_installation_id_env(
        self, tmp_path, monkeypatch
    ):
        key = _generate_test_keypair(tmp_path)
        monkeypatch.setenv("SDLCA_APP_ID", "12345")
        monkeypatch.setenv("SDLCA_APP_PRIVATE_KEY_PATH", str(key))
        monkeypatch.setenv("SDLCA_APP_INSTALLATION_ID_KDTIX_OPEN", "11111")
        monkeypatch.setattr(mint_app_token.Path, "home", lambda: tmp_path)

        call_urls: list[str] = []

        def fake_http(url, token, method="GET"):
            call_urls.append(url)
            return 201, {
                "token": "ghs_ANOTHER",
                "expires_at": "2026-04-22T08:00:00Z",
            }

        with patch.object(mint_app_token, "_http_request", side_effect=fake_http):
            result = mint_app_token.mint_for_org("kdtix-open")

        # Discovery endpoint should NOT be called when env override present
        assert not any(
            u.endswith("/orgs/kdtix-open/installation") for u in call_urls
        )
        # Mint endpoint uses the explicit installation ID
        assert any(u.endswith("/installations/11111/access_tokens") for u in call_urls)
        assert result["installation_id"] == 11111

    def test_mint_fails_loud_on_unknown_org(self, tmp_path, monkeypatch, capsys):
        key = _generate_test_keypair(tmp_path)
        monkeypatch.setenv("SDLCA_APP_ID", "12345")
        monkeypatch.setenv("SDLCA_APP_PRIVATE_KEY_PATH", str(key))
        monkeypatch.setattr(mint_app_token.Path, "home", lambda: tmp_path)
        monkeypatch.delenv("SDLCA_APP_INSTALLATION_ID_NOPE", raising=False)

        def fake_http(url, token, method="GET"):
            return 404, {"message": "Not Found"}

        with patch.object(mint_app_token, "_http_request", side_effect=fake_http):
            with pytest.raises(SystemExit) as exc_info:
                mint_app_token.mint_for_org("nope")
        assert exc_info.value.code == 3
        captured = capsys.readouterr()
        assert "could not discover installation" in captured.err


class TestCli:
    def test_cli_format_env(self, tmp_path, monkeypatch, capsys):
        key = _generate_test_keypair(tmp_path)
        monkeypatch.setenv("SDLCA_APP_ID", "12345")
        monkeypatch.setenv("SDLCA_APP_PRIVATE_KEY_PATH", str(key))
        monkeypatch.setenv("SDLCA_APP_INSTALLATION_ID_KDTIX_OPEN", "11111")
        monkeypatch.setattr(mint_app_token.Path, "home", lambda: tmp_path)

        with patch.object(
            mint_app_token,
            "_http_request",
            return_value=(201, {"token": "ghs_XYZ", "expires_at": "Z"}),
        ):
            rc = mint_app_token.main(["kdtix-open", "--format", "env"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "export GH_TOKEN=ghs_XYZ" in captured.out
        assert "export COPILOT_GITHUB_TOKEN=ghs_XYZ" in captured.out
        assert "expires at Z" in captured.out

    def test_cli_format_token(self, tmp_path, monkeypatch, capsys):
        key = _generate_test_keypair(tmp_path)
        monkeypatch.setenv("SDLCA_APP_ID", "12345")
        monkeypatch.setenv("SDLCA_APP_PRIVATE_KEY_PATH", str(key))
        monkeypatch.setenv("SDLCA_APP_INSTALLATION_ID_KDTIX_OPEN", "11111")
        monkeypatch.setattr(mint_app_token.Path, "home", lambda: tmp_path)

        with patch.object(
            mint_app_token,
            "_http_request",
            return_value=(201, {"token": "ghs_JUST_TOKEN", "expires_at": "Z"}),
        ):
            rc = mint_app_token.main(["kdtix-open", "--format", "token"])
        assert rc == 0
        captured = capsys.readouterr()
        # Single-line: just the token
        assert captured.out.strip() == "ghs_JUST_TOKEN"
