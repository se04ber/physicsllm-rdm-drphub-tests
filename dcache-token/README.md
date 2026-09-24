# Getting a dCache bearer token through HIFIS

How to get a Helmholtz AAI token on your machine and use it to read and
write the PUNCH dCache benchmark space with `rclone`. Everything here runs
in a terminal. Nothing is installed from this repository.

## 0. Be in the PUNCH sub-VO `physicsllm`

Check your access in a browser first:

https://hifis-storage-web.desy.de/

Then open the folders `punch`, `physicsllm`, `01-benchmarks`. If access is
refused, email support@punch4nfdi.de and stop here. Nothing below works
without the VO membership, and approval is the slow step.

## 1. Install the tools

`oidc-agent` handles the login, `rclone` talks to dCache.

```bash
sudo apt-get install oidc-agent
sudo apt-get install rclone
```

In every new shell, start the agent and set the password variable the next
step reads. An empty password is fine, or set one if you prefer.

```bash
eval "$(oidc-agent)"
export OIDC_PASSWD=""
```

## 2. Log in through DESY Keycloak

Once per machine. Opens a browser page where you log in with your Helmholtz
ID.

```bash
oidc-gen \
  --flow=code \
  --client-id=desy-public \
  --client-secret="" \
  --scope="openid profile offline_access" \
  --iss="https://keycloak.desy.de/auth/realms/production/" \
  --redirect-uri="http://localhost:4242" \
  --pw-env=OIDC_PASSWD \
  HIFIS
```

If the browser lands on a page but the terminal does not continue on its
own, paste the full URL of that page back in:

```bash
oidc-gen --codeExchange='<url you landed on>'
```

If a later step answers with permission denied even though step 0 worked,
it is a scope issue on the identity provider side, not something you did.
Report it to one of us so it can be followed up.

## 3. Configure `rclone`

Append this to `~/.config/rclone/rclone.conf`:

```ini
[HIFIS_BENCHMARKS]
type = webdav
url = https://dcache-doma-door01.desy.de/punch/physicsllm/01Benchmarks
vendor = other
bearer_token_command = oidc-token HIFIS
```

Test it:

```bash
rclone lsd HIFIS_BENCHMARKS:
```

A directory listing means everything is configured. From here on `rclone`
fetches a fresh token from `oidc-agent` whenever it needs one.

## 4. Practise on a test folder

Pick a folder name nobody else is likely to choose:

```bash
export TEST="test-folder-$(whoami)-$RANDOM"
```

Upload a single file, or a whole folder such as a benchmark bundle:

```bash
echo 1 > test.txt
rclone copy test.txt "HIFIS_BENCHMARKS:$TEST/" --progress
rclone copy ./example-bench "HIFIS_BENCHMARKS:$TEST" --progress
```

Check what arrived:

```bash
rclone ls "HIFIS_BENCHMARKS:$TEST"
```

Clean up. Always look at the dry run before deleting anything:

```bash
rclone purge --dry-run "HIFIS_BENCHMARKS:$TEST"
rclone purge "HIFIS_BENCHMARKS:$TEST"
```

## 5. A read-only credential for REANA

A REANA job should not carry your personal token. Mint a macaroon instead:
a credential limited to one path, a fixed set of read activities, and an
expiry. This one allows listing and downloading under `01Benchmarks` for a
week.

```bash
M=$(curl -s -X POST \
  -H "Authorization: Bearer $(oidc-token HIFIS)" \
  -H 'Content-Type: application/macaroon-request' \
  -d '{"caveats":["path:/punch/physicsllm/01Benchmarks","activity:DOWNLOAD,LIST,READ_METADATA"],"validity":"PT168H"}' \
  https://dcache-doma-door01.desy.de/ \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['macaroon'])")
reana-client secrets-add --overwrite --env DCACHE_BEARER_TOKEN="$M" && unset M
```

Post to the door root, `https://dcache-doma-door01.desy.de/`, with nothing
after the slash. The URL you post to becomes a path caveat of its own, and
two path caveats permit exactly that one directory and nothing under it.
The resulting refusal looks like a permission problem on the data and is
not.

Uploads keep using your own token through `rclone`, as in step 4. Macaroons
at this door are read-only.
