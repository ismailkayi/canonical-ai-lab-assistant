# ==============================================================================
# CANONICAL AI LAB ASSISTANT — MicroCloud Infrastructure
# Provider: LXD (OpenTofu)
# Orchestration: OpenTofu + Ansible (called from deploy_microcloud.sh)
# ==============================================================================

terraform {
  required_providers {
    lxd = {
      source  = "terraform-lxd/lxd"
      version = "~> 2.4.0"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5.0"
    }
  }
}

provider "lxd" {}

# -----------------------------------------------------------------------
# Variables
# -----------------------------------------------------------------------

variable "user_prefix" {
  description = "User prefix for resource isolation (e.g. 'alice')"
  type        = string
  default     = "lab"
}

variable "ubuntu_image" {
  description = "LXD image reference"
  type        = string
  default     = "ubuntu:24.04"
}

variable "ssh_public_key" {
  description = "Host SSH public key to inject into VMs"
  type        = string
}

variable "lxd_network_name" {
  description = "Primary LXD bridge network (VM eth0)"
  type        = string
}

variable "lxd_storage_pool" {
  description = "LXD storage pool for VM root disks and Ceph disks"
  type        = string
}

variable "microcloud_node_count" {
  description = "Number of MicroCloud nodes (minimum 3 for this automation flow)"
  type        = number
  default     = 3

  validation {
    condition     = var.microcloud_node_count >= 3
    error_message = "microcloud_node_count must be >= 3."
  }
}

variable "microcloud_node_cpu" {
  description = "vCPU count per MicroCloud node"
  type        = number
  default     = 2

  validation {
    condition     = var.microcloud_node_cpu >= 1
    error_message = "microcloud_node_cpu must be >= 1."
  }
}

variable "microcloud_node_memory_mb" {
  description = "Memory in MiB per MicroCloud node"
  type        = number
  default     = 4096

  validation {
    condition     = var.microcloud_node_memory_mb >= 1024
    error_message = "microcloud_node_memory_mb must be >= 1024 MiB."
  }
}

variable "microcloud_root_disk_size_gib" {
  description = "Root disk size in GiB per node"
  type        = number
  default     = 40

  validation {
    condition     = var.microcloud_root_disk_size_gib >= 20
    error_message = "microcloud_root_disk_size_gib must be >= 20."
  }
}

variable "microcloud_ceph_disk_size_gib" {
  description = "Ceph data disk size in GiB per OSD volume"
  type        = number
  default     = 50

  validation {
    condition     = var.microcloud_ceph_disk_size_gib >= 10
    error_message = "microcloud_ceph_disk_size_gib must be >= 10."
  }
}

variable "ceph_disks_per_node" {
  description = "Number of Ceph OSD volumes per node (1 = default, 2-4 for higher throughput)"
  type        = number
  default     = 1

  validation {
    condition     = var.ceph_disks_per_node >= 1 && var.ceph_disks_per_node <= 8
    error_message = "ceph_disks_per_node must be between 1 and 8."
  }
}

variable "local_disk_size_gib" {
  description = "Size of local ZFS disk per node in GiB. Set to 0 to skip local storage (default: 0)."
  type        = number
  default     = 0

  validation {
    condition     = var.local_disk_size_gib == 0 || var.local_disk_size_gib >= 10
    error_message = "local_disk_size_gib must be 0 (disabled) or >= 10 GiB."
  }
}

# -----------------------------------------------------------------------
# Locals
# -----------------------------------------------------------------------

locals {
  # env_id follows the workspace name (e.g. alice_microcloud)
  env_id     = terraform.workspace == "default" ? var.user_prefix : terraform.workspace
  # LXD names must not contain underscores
  lxd_prefix = replace(local.env_id, "_", "-")
}

# -----------------------------------------------------------------------
# LXD base profile
# -----------------------------------------------------------------------

resource "lxd_profile" "lab_base" {
  name = "${local.lxd_prefix}-iac-base"

  config = {
    "security.nesting" = "true"
  }

  device {
    name = "eth0"
    type = "nic"
    properties = {
      network = var.lxd_network_name
    }
  }

  device {
    name = "root"
    type = "disk"
    properties = {
      pool = var.lxd_storage_pool
      path = "/"
      size = "${var.microcloud_root_disk_size_gib}GiB"
    }
  }
}

# -----------------------------------------------------------------------
# OVN uplink bridge (IP-free, dedicated for MicroOVN)
# -----------------------------------------------------------------------

resource "lxd_network" "ovn_uplink" {
  # Limit to 15 chars to stay within Linux bridge name limits
  name = "mc-${substr(local.lxd_prefix, 0, 8)}-up"
  type = "bridge"
  config = {
    "ipv4.address" = "none"
    "ipv6.address" = "none"
  }
}

# -----------------------------------------------------------------------
# Ceph block volumes (ceph_disks_per_node per node)
# Total count = node_count × ceph_disks_per_node
# Volume naming: {prefix}-ceph-{node}-{osd}  (1-indexed both)
# -----------------------------------------------------------------------

locals {
  ceph_volumes = [
    for pair in setproduct(
      range(var.microcloud_node_count),
      range(var.ceph_disks_per_node)
    ) : {
      node = pair[0]  # 0-based node index
      osd  = pair[1]  # 0-based OSD index within node
    }
  ]
}

resource "lxd_volume" "microcloud_ceph_disks" {
  count        = length(local.ceph_volumes)
  name         = "${local.lxd_prefix}-ceph-${local.ceph_volumes[count.index].node + 1}-${local.ceph_volumes[count.index].osd + 1}"
  pool         = var.lxd_storage_pool
  content_type = "block"
  config       = { size = "${var.microcloud_ceph_disk_size_gib}GiB" }
}

# -----------------------------------------------------------------------
# Local ZFS volumes (one per node, optional — skipped when local_disk_size_gib=0)
# -----------------------------------------------------------------------

resource "lxd_volume" "microcloud_local_disks" {
  count        = var.local_disk_size_gib > 0 ? var.microcloud_node_count : 0
  name         = "${local.lxd_prefix}-local-${count.index + 1}"
  pool         = var.lxd_storage_pool
  content_type = "block"
  config       = { size = "${var.local_disk_size_gib}GiB" }
}

# -----------------------------------------------------------------------
# MicroCloud VM nodes
# -----------------------------------------------------------------------

resource "lxd_instance" "microcloud_nodes" {
  count    = var.microcloud_node_count
  name     = "${local.lxd_prefix}-node-${count.index + 1}"
  image    = var.ubuntu_image
  type     = "virtual-machine"
  profiles = [lxd_profile.lab_base.name]

  limits = {
    cpu    = tostring(var.microcloud_node_cpu)
    memory = "${var.microcloud_node_memory_mb}MiB"
  }

  # eth1 — dedicated OVN uplink NIC (no IP)
  device {
    name = "eth1"
    type = "nic"
    properties = {
      network = lxd_network.ovn_uplink.name
    }
  }

  # Ceph OSD block devices: one device per OSD attached to this node.
  # Index math: OSD volumes for node N occupy positions
  #   [N * ceph_disks_per_node .. (N+1) * ceph_disks_per_node - 1]
  # in the flat lxd_volume.microcloud_ceph_disks list.
  dynamic "device" {
    for_each = range(var.ceph_disks_per_node)
    content {
      name = "ceph-disk-${device.value + 1}"
      type = "disk"
      properties = {
        source = lxd_volume.microcloud_ceph_disks[count.index * var.ceph_disks_per_node + device.value].name
        pool   = var.lxd_storage_pool
      }
    }
  }

  # Optional local ZFS disk (only when local_disk_size_gib > 0)
  dynamic "device" {
    for_each = var.local_disk_size_gib > 0 ? [1] : []
    content {
      name = "local-disk"
      type = "disk"
      properties = {
        source = lxd_volume.microcloud_local_disks[count.index].name
        pool   = var.lxd_storage_pool
      }
    }
  }

  config = {
    "user.user-data" = <<-EOT
      #cloud-config
      ssh_authorized_keys:
        - ${var.ssh_public_key}
    EOT
  }
}

# -----------------------------------------------------------------------
# Ansible inventory (lxd connection — no SSH needed from host)
# -----------------------------------------------------------------------

resource "local_file" "ansible_inventory" {
  content = yamlencode({
    all = {
      children = {
        microcloud = {
          hosts = {
            for name in lxd_instance.microcloud_nodes[*].name :
            name => { ansible_connection = "lxd" }
          }
        }
      }
    }
  })
  filename        = "${path.module}/../inventory_${local.env_id}.yaml"
  file_permission = "0644"
}

# -----------------------------------------------------------------------
# Outputs
# -----------------------------------------------------------------------

output "env_id" {
  value       = local.env_id
  description = "Environment identifier (workspace name)"
}

output "node_names" {
  value       = lxd_instance.microcloud_nodes[*].name
  description = "LXD instance names for all MicroCloud nodes"
}

output "ovn_uplink_network" {
  value       = lxd_network.ovn_uplink.name
  description = "Name of the OVN uplink bridge created for this environment"
}

output "inventory_file" {
  value       = local_file.ansible_inventory.filename
  description = "Path to the generated Ansible inventory file"
}
