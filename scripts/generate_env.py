#!/usr/bin/env python3
"""
Generate environment-specific .env files from a .mdix master template.

Replaces the two inline Python blocks from generate-env.yml.

Usage (local):
  python3 scripts/generate_env.py --env dev
  python3 scripts/generate_env.py --env prod --dry-run
  python3 scripts/generate_env.py --env staging --output-dir ./output

Usage (from GitHub Actions — env vars are set by the workflow):
  python3 scripts/generate_env.py
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Generate .env files from a .mdix master template."
    )
    p.add_argument(
        "--env", "--environment",
        dest="env",
        default=os.environ.get("TARGET_ENV", "dev"),
        choices=["dev", "staging", "prod"],
        help="Target environment (default: dev)",
    )
    p.add_argument(
        "--master-template",
        default=os.environ.get(
            "MASTER_TEMPLATE",
            ".mdix/env/templates/env.master.mdix",
        ),
        help="Path to the master .mdix template",
    )
    p.add_argument(
        "--secrets-json",
        default=os.environ.get("SECRETS_JSON", "/tmp/secrets.json"),
        help="Path to decrypted secrets JSON (or {} if none)",
    )
    p.add_argument(
        "--output-dir",
        default=os.environ.get("OUTPUT_DIR", "output"),
        help="Directory to write generated .env files (default: output)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=os.environ.get("DRY_RUN", "false").lower() == "true",
        help="Preview without writing files",
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# Phase 1 — Patch the template and compile it to JSON
# ---------------------------------------------------------------------------

def patch_and_compile(master_template, target_env):
    """
    Patch current_env<enum> in the master template, run mdix convert,
    and return the resulting dict.
    """
    env_map = {
        "dev":     "Env.DEV",
        "staging": "Env.STAGING",
        "prod":    "Env.PROD",
    }
    if target_env not in env_map:
        print(f"ERROR: Unknown environment '{target_env}'", file=sys.stderr)
        sys.exit(1)

    enum_value = env_map[target_env]
    print(f"Target environment : {target_env}")
    print(f"Enum value         : {enum_value}")

    if not os.path.exists(master_template):
        print(
            f"ERROR: Template not found: {master_template}",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(master_template) as fh:
        source = fh.read()

    patched = re.sub(
        r"(current_env<enum>\s*=\s*)Env\.\w+",
        rf"\g<1>{enum_value}",
        source,
    )

    if patched == source:
        print(
            "WARNING: could not find 'current_env<enum>' line to patch",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Patched  : current_env<enum> = {enum_value}")

    patched_path = f"/tmp/env.{target_env}.mdix"
    json_path    = f"/tmp/env.{target_env}.json"

    with open(patched_path, "w") as fh:
        fh.write(patched)

    result = subprocess.run(
        ["mdix", "convert", patched_path, "--to", "json", "-o", json_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(
            f"ERROR: mdix convert failed:\n{result.stderr}",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(json_path) as fh:
        data = json.load(fh)

    print("\nResolved keys:")
    for k, v in sorted(data.items()):
        print(f"  {k} = {str(v)[:80]}")

    return data


# ---------------------------------------------------------------------------
# Phase 2 — Merge secrets and generate the .env file
# ---------------------------------------------------------------------------

def resolve_secret(secrets, group, field, env):
    """
    Look for secrets['group.field_env'] first,
    then fall back to secrets['group.field'].
    """
    keyed   = secrets.get(f"{group}.{field}_{env}")
    generic = secrets.get(f"{group}.{field}")
    return keyed if keyed is not None else generic


def generate_dotenv(config, secrets, target_env, output_dir, dry_run):
    """Inject secrets into config and write the .env file."""

    PLACEHOLDER = "INJECT_FROM_SECRETS"

    # Fields that need to be overwritten from the secrets file
    injections = {
        "database.password":     resolve_secret(secrets, "database", "password",    target_env),
        "redis.password":        resolve_secret(secrets, "redis",    "password",    target_env),
        "auth.jwt_secret":       resolve_secret(secrets, "auth",     "jwt_secret",  target_env),
        "services.stripe_key":   resolve_secret(secrets, "services", "stripe_key",  target_env),
        "services.sendgrid_key": resolve_secret(secrets, "services", "sendgrid_key", target_env),
        "services.sentry_dsn":   resolve_secret(secrets, "services", "sentry_dsn",  target_env),
    }

    for dotted_key, secret_value in injections.items():
        if secret_value and config.get(dotted_key) == PLACEHOLDER:
            config[dotted_key] = secret_value

    # Keys to exclude from the .env output (meta fields, not runtime config)
    SKIP_KEYS = {"current_env", "environment", "generated_by", "project"}

    def format_env_value(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, str) and (" " in v or "=" in v or "#" in v):
            return f'"{v}"'
        if isinstance(v, (dict, list)):
            return None  # skip — already flattened by mdix convert
        return str(v)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "# Generated by mdix-envgen",
        f"# Environment  : {target_env.upper()}",
        f"# Generated at : {now}",
        "# Source       : .mdix/env/templates/env.master.mdix",
        "# DO NOT EDIT  — edit the .mdix template instead",
        "",
    ]

    last_prefix = None
    for raw_key in sorted(config.keys()):
        if raw_key in SKIP_KEYS:
            continue
        value = config[raw_key]
        formatted = format_env_value(value)
        if formatted is None:
            continue

        # "database.host" → "DATABASE_HOST"
        env_key = raw_key.replace(".", "_").upper()

        # Blank line between table groups for readability
        prefix = raw_key.split(".")[0] if "." in raw_key else "_flat"
        if last_prefix is not None and prefix != last_prefix:
            lines.append("")
        last_prefix = prefix

        lines.append(f"{env_key}={formatted}")

    output = "\n".join(lines) + "\n"

    out_path = os.path.join(output_dir, f".env.{target_env}")
    print(f"\n=== Preview: {out_path} ===\n")
    print(output)
    print("=" * 56)

    if dry_run:
        print("DRY RUN — file not written.")
    else:
        os.makedirs(output_dir, exist_ok=True)
        with open(out_path, "w") as fh:
            fh.write(output)
        print(f"Written: {out_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(args):
    config = patch_and_compile(args.master_template, args.env)

    # Load decrypted secrets (written by the workflow's decrypt step)
    secrets = {}
    if os.path.exists(args.secrets_json):
        with open(args.secrets_json) as fh:
            try:
                secrets = json.load(fh)
            except json.JSONDecodeError:
                print(
                    f"WARNING: Could not parse secrets JSON at {args.secrets_json}",
                    file=sys.stderr,
                )
    else:
        print("No secrets JSON found — INJECT_FROM_SECRETS fields will remain as placeholders.")

    generate_dotenv(config, secrets, args.env, args.output_dir, args.dry_run)


if __name__ == "__main__":
    run(parse_args())
