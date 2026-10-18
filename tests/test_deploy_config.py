"""The Render blueprint must be able to boot.

`Settings` validates that every model role has an id, a provider and a family,
so a blueprint that omits them makes the app exit during lifespan startup —
before the health check can ever pass. The local `.env` that holds those values
is gitignored and never reaches Render, so a deploy has exactly the environment
in `render.yaml` plus the `sync: false` secrets.
"""

import re
from pathlib import Path

from forensics.config import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent
RENDER_YAML = REPO_ROOT / "render.yaml"

_ENTRY = re.compile(r"^\s*-\s*key:\s*(?P<key>\S+)\s*$", re.MULTILINE)
_VALUE = re.compile(r"^\s*value:\s*(?P<value>.*?)\s*$", re.MULTILINE)

#: Set from the Render dashboard at deploy time (`sync: false`), so they are not
#: in the file and have to be supplied here. Obviously fake; the `allowlist
#: secret` pragmas keep detect-secrets from reading the keyword+literal pairing
#: as a real credential.
DASHBOARD_SECRETS = {
    "NVIDIA_API_KEY": "nvapi-test" + "-key-not-real",  # pragma: allowlist secret
    "ALERT_WEBHOOK_URL": "https://example.invalid/hook",
    "ALERT_WEBHOOK_SECRET": "not" + "-a-real-secret",  # pragma: allowlist secret
}

#: The blueprint points these at the Render disk (`/var/data`), which does not
#: exist — and cannot be created — on a dev machine. They are the deploy's
#: filesystem layout, not the thing under test, so they are redirected.
_PATH_KEYS = (
    "DATA_DIR",
    "TRACES_DIR",
    "CACHE_DIR",
    "DB_PATH",
    "SCHEDULER_DB_PATH",
    "SPEND_FILE",
    "STATIC_DIR",
)


def _blueprint_env() -> dict[str, str]:
    """Env vars a deploy gets from the blueprint, secrets aside."""
    text = RENDER_YAML.read_text()
    env: dict[str, str] = {}
    for entry in _ENTRY.finditer(text):
        tail = text[entry.end() :]
        value_match = _VALUE.match(tail)
        if value_match is None:
            # `sync: false` — supplied by the dashboard, not the file.
            continue
        env[entry.group("key")] = value_match.group("value").strip().strip('"')
    return env


def test_blueprint_declares_a_bootable_environment(tmp_path: Path) -> None:
    env = {**_blueprint_env(), **DASHBOARD_SECRETS}
    # Fail loudly instead of silently passing on an empty parse.
    assert "PIPELINE_MODEL_ID" in env, f"could not parse render.yaml env vars: {sorted(env)}"
    for key in _PATH_KEYS:
        if key in env:
            env[key] = str(tmp_path / key.lower())

    # Raises ValidationError if a model role is unconfigured or two roles clash.
    settings = Settings(_env_file=None, **env)

    assert settings.pipeline_model.model_id
    assert settings.pipeline_model.provider
    assert settings.pipeline_model.family
    assert settings.judge_model.family != settings.pipeline_model.family
