# Local snap feasibility prototype

This branch is isolated from normal product development. The initial prototype
uses classic confinement and targets Ubuntu 24.04 on amd64.

## Prototype plan

1. Package the Python CLI and immutable scripts, playbooks, and Terraform files.
2. Bundle OpenTofu 1.12.6, Ansible Core 2.20.1, `community.general` 12.1.0,
   and the locked `local` 2.5.3 and LXD 2.4.0 providers.
3. Copy Terraform configuration from `$SNAP/terraform` to
   `$SNAP_USER_COMMON/terraform`; keep provider data, workspaces, state, plans,
   logs, history, and generated inventories writable outside `$SNAP`.
4. Keep `lab-ai doctor` read-only. In snap mode, require the explicit
   `lab-ai bootstrap --host-setup` command before apt, snap, LXD, or group
   changes are allowed.
5. Build locally, install with `--dangerous --classic`, then verify `doctor`,
   `check`, `chat`, deploy, health, and delete in a clean Ubuntu 24.04 VM using
   a dedicated lab prefix.

Build artifacts (`*.snap`, `parts/`, `prime/`, and `stage/`) are ignored and
must not be committed.

The prototype has been built with Snapcraft's managed Ubuntu 24.04 build
environment. The packaged `doctor`, `check`, `chat`, OpenTofu provider mirror,
Ansible Core, and `community.general` paths have been smoke-tested without
installing or mutating lab infrastructure. Deployment, health, and deletion
remain clean-VM acceptance tests.

Prototype revision `0.1.0-prototype.1` also validates LXD storage from its
byte-level pool metrics and excludes stopped instances from active CPU/RAM
consumption. This prevents stopped Snapcraft build VMs and newer LXD storage
output from falsely reducing deployable capacity to zero.

Revision `0.1.0-prototype.2` keeps named sizing tiers immutable and accounts
for every Ceph OSD and local disk during host-aware sizing. For example, a
three-node small tier with two 50 GiB OSDs per node is consistently planned and
validated as 420 GiB rather than silently increasing each OSD to 200 GiB.

## Build and install

```bash
snapcraft
sudo snap install ./lab-ai_0.1.0-prototype.2_amd64.snap \
  --dangerous --classic
lab-ai doctor
```

LXD and `gemma4` remain separately installed platform services. Do not reuse
staging lab prefixes or Terraform state when testing this prototype.
