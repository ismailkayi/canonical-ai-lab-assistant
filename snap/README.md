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

The current prototype was built and installed in a dedicated Ubuntu 24.04 VM
with nested virtualization. Packaged `doctor`, `check`, `chat`, the shared sizing
CLI, Ansible, and the OpenTofu provider mirror passed acceptance checks. Two
concurrent labs, add-node, scale with AI-gated RAM fallback, health, and deletion
were exercised without touching existing host labs. A local update from
`prototype.4` preserved the existing lab's Terraform lineage, serial, geometry,
and inventory hash. Offline provider initialization also passed.

Prototype revision `0.1.0-prototype.1` also validates LXD storage from its
byte-level pool metrics and excludes stopped instances from active CPU/RAM
consumption. This prevents stopped Snapcraft build VMs and newer LXD storage
output from falsely reducing deployable capacity to zero.

Revision `0.1.0-prototype.2` keeps named sizing tiers immutable and accounts
for every Ceph OSD and local disk during host-aware sizing. For example, a
three-node small tier with two 50 GiB OSDs per node is consistently planned and
validated as 420 GiB rather than silently increasing each OSD to 200 GiB.

Revision `0.1.0-prototype.3` makes concurrent lab naming deterministic. A
workspace-like deployment name is normalized into a unique prefix (for example,
`lab_microcloud_2` becomes `lab-2_microcloud`) instead of being rejected or
silently reusing the first lab's `lab` prefix.

Revision `0.1.0-prototype.4` introduced deterministic overcommit capability
answers. Its CPU limit, fixed-tier behavior, and broad workload rejection rules
are superseded by the current revision.

## Current revision: 0.1.0-prototype.5

The October staging update is integrated into the snap prototype:

- One dynamic sizing helper drives advice, approval, and shell deployment;
  explicit resource values are preserved, while tier names express intent.
- All active LXD projects share a deterministic 3:1 CPU allocation ceiling.
  CPU-only overcommit requires a contention warning and approval, not an AI veto.
- RAM retains its normal reserve; RAM shortages can receive a separately
  AI-assessed 1.25:1 fallback for deployment and expansion. Assessment focuses on
  peak memory risk, not the presence of the word benchmark.
- `dataset_size_gib` accounts for three replicas and 20% free Ceph space.
  Root, raw Ceph, local disk, usable Ceph, and dataset budgets share exact totals.
- LXD JSON resource metrics fail closed; stopped instance limits are reported
  separately and capacity is checked again before approved operations.
- The approved storage pool reaches the deployer, and hidden `resolve-sizing`
  uses the bundled Python and modules rather than a host venv or pip.
- Writable Terraform state, provider mirroring, explicit host setup, and
  concurrent-lab prefix normalization remain snap-specific safeguards.
- Packaged lifecycle scripts are executable, including health and add-node
  helpers invoked directly by other scripts. Source mode ignores `SNAP`
  inherited from unrelated apps such as the snap-packaged VS Code editor.

Acceptance used the existing `gemma4` service through a private SSH tunnel into
the test VM; it did not reconfigure the host model service. Ceph can report a
short-lived recovery warning after an expansion: re-run health after convergence
rather than treating a partial postcondition as success.

## Build and install

```bash
snapcraft pack
sudo snap install ./lab-ai_0.1.0-prototype.5_amd64.snap \
  --dangerous --classic
lab-ai doctor
```

## Updating an installed local prototype

Exit the chat and wait for ongoing infrastructure operations. Back up state to
an ignored directory inside the snap worktree, then install the new local file
over the existing snap:

```bash
cd ~/lxdlab/canonical-ai-lab-assistant-snap
backup=".snap-test/state-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup"
cp -a "$HOME/snap/lab-ai/common" "$backup/common"
sudo snap install ./lab-ai_0.1.0-prototype.5_amd64.snap --dangerous --classic
lab-ai doctor
```

This refreshes the local snap revision without purging user data. Confirm that
existing Terraform workspaces and inventories remain available before creating
new labs. Do not use `snap remove --purge lab-ai` while managed labs exist:
purge deletes Terraform state but cannot delete external LXD resources. Existing
VM geometry is not changed by installing this revision.

LXD and `gemma4` remain separately installed platform services. Do not reuse
staging lab prefixes or Terraform state when testing this prototype.
