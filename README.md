# mdix-envgen

Generate environment-specific `.env` files from a single DixScript template.

## The architecture
templates/
env.master.mdix          ← one file, all environments, safe to commit
env.secrets.mdix         ← real secrets, NEVER commit
env.secrets.mdix.enc     ← encrypted secrets, safe to commit

output/
.env.dev                 ← generated
.env.staging             ← generated
.env.prod                ← generated
## How the switch works

The master template has one line that controls everything:

```dixscript
current_env<enum> = Env.DEV
```

Every host, port, flag, and URL is derived by a QuickFunc that takes
`current_env` as its argument. The workflow patches that one line to
`Env.STAGING` or `Env.PROD` before compiling. You never edit that line
manually — you only edit the QuickFuncs when the logic changes.

## Usage

**Actions → Generate .env files → Run workflow**

| Input | Options | Description |
|---|---|---|
| `environment` | `dev` `staging` `prod` | Which environment to generate |
| `dry_run` | `true` `false` | Preview without writing |

## Secrets setup

1. Fill in `templates/env.secrets.mdix` with real values
2. Set `MDIX_ENV_PASSWORD` in your GitHub repository secrets
3. Run locally: `mdix compile templates/env.secrets.mdix --password`
4. Commit `templates/env.secrets.mdix.enc`
5. Delete or gitignore `templates/env.secrets.mdix`

Real secret values in the master template are marked `INJECT_FROM_SECRETS`.
The workflow decrypts the `.enc` file and merges the right per-environment
value into the output automatically.

## Adding a new environment

Add one new enum value and one new branch to each QuickFunc:

```dixscript
@ENUMS(
  Env { DEV = 0, STAGING = 1, PROD = 2, CANARY = 3 }  // ← add here
)

~dbHost<string>(env<enum>) {
  return env == Env.PROD    ? "db.prod.internal"    :
         env == Env.STAGING ? "db.staging.internal" :
         env == Env.CANARY  ? "db.canary.internal"  : // ← add here
                               "localhost"
}
```

Then trigger the workflow with `environment = canary`.
