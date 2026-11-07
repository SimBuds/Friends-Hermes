# Friends-Hermes

[Hermes Agent](https://github.com/NousResearch/hermes-agent) in a QEMU VM,
answering a few friends in a private Discord server as a sports-betting
research assistant: odds and lines across books, scores, schedules, and
stats. It does not bet, and every number comes from a documented API with
its source and fetch time. Information, not betting advice.

It uses the Local-LLM router on the host (`qwen`, context 131,072) and runs
only when started by hand.

[`PLAN.md`](PLAN.md) holds the why. [`AGENTS.md`](AGENTS.md) holds the rules
for coding agents.

## Setup guide

These steps write outside this repo (`~/VMs/friends-hermes`, a disk image,
installs in the guest). Each block says where it runs. The VM lives in its
own folder because Auto-Agent's VM already uses the name
`debian-13.7.0-netinst` in `~/VMs`.

### 1. Check the host

```bash
# Runs in: local terminal, host. Read-only.
quickemu --version                        # 4.9.9 was used to write this guide
curl -s http://127.0.0.1:8080/health      # must print {"status":"ok"}
free -g                                   # "available" must leave room for a 4 GB VM next to qwen
ss -ltn | grep ':22221 '                  # must print nothing: the port is free
```

### 2. Create the VM

```bash
# Runs in: local terminal, host. Creates ~/VMs/friends-hermes. Safe to re-run: it replaces the four sizing lines.
mkdir -p ~/VMs/friends-hermes && cd ~/VMs/friends-hermes
quickget debian 13.7.0 netinst
CONF=debian-13.7.0-netinst.conf          # EDIT if quickget wrote a different name
sed -i '/^ram=/d;/^cpu_cores=/d;/^disk_size=/d;/^ssh_port=/d' "$CONF"
printf 'ram="4G"\ncpu_cores="2"\ndisk_size="20G"\nssh_port="22221"\n' >> "$CONF"
cat "$CONF"                               # must show the four lines above
quickemu --vm "$CONF" --public-dir none   # boots the installer
```

`ssh_port` is pinned because quickemu otherwise takes the first free port
from 22220, which is Auto-Agent's whenever its VM is off.

In the installer, choose no desktop, the SSH server and standard utilities.
Set the hostname to `friends-hermes` and the user to `hermes`. Leave the
root password blank, so your user gets `sudo`. Shut down with
`sudo poweroff` when it finishes.

### 3. Snapshot the clean install

```bash
# Runs in: local terminal, host, in ~/VMs/friends-hermes, with the VM shut down.
quickemu --vm debian-13.7.0-netinst.conf --snapshot create clean
quickemu --vm debian-13.7.0-netinst.conf --snapshot info   # must list "clean"
```

### 4. Reach the router from the VM

Boot the VM (see [Connecting](#connecting)), log in, then:

```bash
# Runs in: inside the VM.
sudo apt update && sudo apt install -y curl git
curl -s http://10.0.2.2:8080/health                               # must print {"status":"ok"}
curl -s http://10.0.2.2:8080/v1/models | grep -o '"id":"[a-z]*"'  # must list "qwen"
```

`10.0.2.2` is QEMU's address for the host's loopback. If the first `curl`
fails, stop: nothing after this works without it.

### 5. Install Hermes, reading the installer first

```bash
# Runs in: inside the VM, as hermes.
curl -fsSL https://hermes-agent.nousresearch.com/install.sh -o install.sh
sha256sum install.sh          # compare with the hash of the copy you reviewed
less install.sh               # read what it installs
bash install.sh --non-interactive
source ~/.bashrc
hermes --version              # this guide was written for v0.23.0
```

`--non-interactive` skips API-key setup and the gateway service.

### 6. Point Hermes at the router

```bash
# Runs in: inside the VM. Safe to re-run: each line overwrites the same key.
hermes config set model.provider custom
hermes config set model.base_url http://10.0.2.2:8080/v1
hermes config set model.default qwen
hermes config set model.api_key local
hermes config set model.context_length 131072
hermes config set skills.write_approval true
hermes config set auxiliary.background_review.enabled false
# Each key's section and value, comments skipped. Must show model: for base_url, default and context_length, and skills: for write_approval.
awk '/^[a-z_]+:/{sec=$0} /^[[:space:]]+(base_url|default|context_length|write_approval):/ && !/^[[:space:]]*#/{print NR": "sec" -> "$0}' ~/.hermes/config.yaml
grep -n -A1 '^  background_review:' ~/.hermes/config.yaml   # the line after must show enabled: false
grep -n -A2 '^local_runtime:' ~/.hermes/config.yaml          # nothing, or enabled: false
```

This block is the durable copy of Hermes's base config. Re-run it after
restoring an older snapshot. Each key is in [Hermes config](#hermes-config).

### 7. Check it, then snapshot

Run `hermes` and send:

```text
Reply with the single word: ready
```

It must answer `ready`, and the router's log on the host must show the
request. Then shut the VM down and snapshot it:

```bash
# Runs in: local terminal, host, in ~/VMs/friends-hermes. Refuses while this VM is up.
PIDF=debian-13.7.0-netinst/debian-13.7.0-netinst.pid    # this VM's own pid file. Both VMs share one process name
if [ -r "$PIDF" ] && kill -0 "$(cat "$PIDF")"; then echo "STOP: the VM is still running (pid $(cat "$PIDF"))."; else
  quickemu --vm debian-13.7.0-netinst.conf --snapshot create hermes-configured
  quickemu --vm debian-13.7.0-netinst.conf --snapshot info   # must list clean and hermes-configured
fi
```

### Hermes config

| Key                                     | Value                       | Why                                                          |
| --------------------------------------- | --------------------------- | ------------------------------------------------------------ |
| `model.provider`                      | `custom`                  | Any OpenAI-compatible server                                 |
| `model.base_url`                      | `http://10.0.2.2:8080/v1` | The host's router, through QEMU's address for the host       |
| `model.default`                       | `qwen`                    | One pinned model, so the router never swaps                  |
| `model.api_key`                       | `local`                   | A placeholder: the router has no auth                        |
| `model.context_length`                | `131072`                  | The router's served context, stated not guessed              |
| `skills.write_approval`               | `true`                    | Skills Hermes writes wait for approval (`/skills pending`) |
| `auxiliary.background_review.enabled` | `false`                   | No skills or notes drafted on its own from friends' chats    |

Leave `local_runtime` off: a second llama.cpp would fight the router for
VRAM.

### Removing it

```bash
# Runs in: local terminal, host. Deletes this VM and its disk. Not undoable. Auto-Agent's VM is not touched.
rm -ri ~/VMs/friends-hermes
```

## Connecting

Start the VM in its own terminal and leave it open. Shut Auto-Agent's VM
down first: both together with `qwen` loaded push the host into swap.

```bash
# Runs in: local terminal, host, in ~/VMs/friends-hermes.
quickemu --vm debian-13.7.0-netinst.conf --public-dir none                   # normal session
quickemu --vm debian-13.7.0-netinst.conf --snapshot apply hermes-configured  # reset (VM shut down)
```

```bash
# Runs in: local terminal, host. A shell in the VM.
ssh -p 22221 hermes@localhost
```

quickemu forwards SSH on all of the host's interfaces, so use a key and
turn off password login. The key is your host user's existing public key.
It gives the VM nothing, since only the public half is copied.

```bash
# Runs in: local terminal, host, with the VM running. Safe to run twice.
ssh-copy-id -p 22221 hermes@localhost
ssh -p 22221 -o BatchMode=yes hermes@localhost true; echo "key check exit=$?"   # must print 0
```

```bash
# Runs in: inside the VM. Keys only for SSH. Safe to run twice.
echo 'PasswordAuthentication no' | sudo tee /etc/ssh/sshd_config.d/10-no-password.conf
sudo sshd -t && echo "sshd config OK"
sudo systemctl reload ssh
```

Discord setup is written in Phase 3.

## Tools

Written in Phases 4 to 6 (odds and stats collectors and the plugin).

## Tests

None yet.
