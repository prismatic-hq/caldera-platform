# GitHub App for ARC runners and workflows

The ARC runner scale sets register with GitHub as a GitHub App. The `golden-image` and preview
workflows use the same app to read `caldera-platform`, `tremor-api` and `steward-api`. There is one scale set per
repository: `caldera-platform` and every `repo` in `services.yaml`. An org owner creates the app
once. Its credentials go to SSM Parameter Store, and External Secrets syncs them into the
`github-app` Secret.

## Create the app

1. Open **Org Settings -> Developer settings -> GitHub Apps -> New GitHub App**
   (`https://github.com/organizations/prismatic-hq/settings/apps/new`).
2. Set **GitHub App name** (for example `prismatic-hq-arc`) and **Homepage URL**
   (`https://github.com/prismatic-hq/caldera-platform`).
3. Under **Webhook**, clear **Active**. ARC polls GitHub and needs no webhook.
4. Set **Repository permissions**:
   - **Administration**: Read and write (registers repository runners)
   - **Contents**: Read-only (workflows check out and read the other repos)
   - **Metadata**: Read-only
   - Leave every other permission set to **No access**.
5. Under **Where can this GitHub App be installed?**, select **Only on this account**, then
   click **Create GitHub App**.
6. Copy the **App ID** and the **Client ID** (`Iv23...`) from the app's General page. Do not use
   the **Client secret**; nothing here uses it.
7. Under **Private keys**, click **Generate a private key**. The browser downloads
   `<app-name>.<date>.private-key.pem`.

## Install the app

1. On the app page, open **Install App**, then click **Install** next to `prismatic-hq`.
2. Choose **Only select repositories** and pick `caldera-platform`, `tremor-api` and
   `steward-api`. Add each new service repo here when you add it to `services.yaml`.
3. Get the numeric installation ID. It is the number at the end of the install page URL
   (`.../settings/installations/<id>`). You can also run:

   ```sh
   gh api /orgs/prismatic-hq/installations --jq '.installations[] | {app_id, id}'
   ```

## Store the credentials

Keep the app in 1Password first (next section). Run this after every fresh deploy. The network
sweeper deletes `/prismatic/` on destroy.

```sh
mise run secrets:put
```

It reads `app_id`, `installation_id` and `pem` with `op read` and writes them to SSM as
`SecureString` parameters. `--op-vault` and `--op-item` select another item; `--app-id`,
`--installation-id` and `--private-key-file` bypass 1Password. Never paste the key into chat,
tickets or commits.

## Keep the app in 1Password

Store the app as one item in the `Prismatic` vault, named `prismatic-hq GitHub App`, with these
field names so the tasks can read it:

| Field | Value |
|---|---|
| `app_id` | App ID |
| `client_id` | Client ID (`Iv23...`) |
| `installation_id` | Installation ID |
| `pem` | The `.pem` private key, attached as a file named `pem` |

## Store the workflow secrets

Private repositories on the GitHub Free plan cannot read organization secrets or variables, so
each repo gets its own copy. Enable the 1Password desktop app CLI integration first
([DEVELOPMENT.md](DEVELOPMENT.md#1password-cli)), then run this once, and again after rotating
the key:

```sh
mise run secrets:github -- --region us-east-2
```

It reads `client_id` and `pem` with `op read` and sets the `CALDERA_APP_CLIENT_ID` and
`CALDERA_APP_PRIVATE_KEY` secrets and the `AWS_REGION` variable on `caldera-platform` and every
repo in `services.yaml`. `--region` defaults to `AWS_REGION`. Values go to `gh` on stdin, never on
the command line or in output. You need admin access to each repo. `--op-vault` and `--op-item`
select another item; `--client-id` and `--private-key-file` bypass 1Password.

Each job mints a one-hour, contents-read installation token with
`actions/create-github-app-token`.

## Rotate the key

1. Generate a new private key on the app page.
2. Replace the `pem` file on the 1Password item, then run `mise run secrets:put` and
   `mise run secrets:github -- --region us-east-2`.
3. Delete the old key on the app page.
