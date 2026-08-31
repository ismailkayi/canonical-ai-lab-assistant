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

variable "resource_namespace" {
  description = "Eight-hex collision-resistant namespace resolved before deployment"
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{8}$", var.resource_namespace))
    error_message = "resource_namespace must contain exactly eight lowercase hex characters."
  }
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

variable "microcloud_network_mode" {
  description = "MicroCloud network layout: standard-2nic or fully-segregated-4nic"
  type        = string
  default     = "standard-2nic"

  validation {
    condition     = contains(["standard-2nic", "fully-segregated-4nic"], var.microcloud_network_mode)
    error_message = "microcloud_network_mode must be standard-2nic or fully-segregated-4nic."
  }
}

variable "microcloud_ovn_underlay_cidr" {
  description = "Static IPv4 subnet for the dedicated OVN Geneve underlay"
  type        = string
  default     = ""

  validation {
    condition = (
      var.microcloud_network_mode == "standard-2nic" ||
      try(
        can(cidrhost(var.microcloud_ovn_underlay_cidr, var.microcloud_node_count + 9)) &&
        var.microcloud_node_count + 9 < pow(2, 32 - tonumber(split("/", var.microcloud_ovn_underlay_cidr)[1])) - 1,
        false
      )
    )
    error_message = "microcloud_ovn_underlay_cidr must provide a non-broadcast address for every node."
  }
}

variable "microcloud_ceph_network_cidr" {
  description = "Static IPv4 subnet shared by Ceph public and internal traffic"
  type        = string
  default     = ""

  validation {
    condition = (
      var.microcloud_network_mode == "standard-2nic" ||
      try(
        can(cidrhost(var.microcloud_ceph_network_cidr, var.microcloud_node_count + 9)) &&
        var.microcloud_node_count + 9 < pow(2, 32 - tonumber(split("/", var.microcloud_ceph_network_cidr)[1])) - 1,
        false
      )
    )
    error_message = "microcloud_ceph_network_cidr must provide a non-broadcast address for every node."
  }
}

# -----------------------------------------------------------------------
# Locals
# -----------------------------------------------------------------------

locals {
  # env_id follows the workspace name (e.g. alice_microcloud)
  env_id = terraform.workspace == "default" ? var.user_prefix : terraform.workspace
  # LXD names must not contain underscores
  lxd_prefix            = replace(local.env_id, "_", "-")
  segregated_networking = var.microcloud_network_mode == "fully-segregated-4nic"
  nic_planes            = ["mgmt0", "ovn-uplink", "ovn-underlay", "ceph-general"]
  node_macs = [
    for node_index in range(var.microcloud_node_count) : {
      for plane in local.nic_planes :
      plane => join(":", concat(
        ["02", "00"],
        regexall("..", substr(md5("${local.env_id}-${node_index + 1}-${plane}"), 0, 8))
      ))
    }
  ]
}

# -----------------------------------------------------------------------
# LXD base profile
# -----------------------------------------------------------------------

resource "lxd_profile" "lab_base" {
  name = "${local.lxd_prefix}-iac-base"

  config = {
    "security.nesting"                      = "true"
    "user.canonical-ai-lab-assistant.owner" = local.env_id
    "user.canonical-ai-lab-assistant.role"  = "base-profile"
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
  name = "ca-${var.resource_namespace}-up"
  type = "bridge"
  config = {
    "ipv4.address"                          = "none"
    "ipv6.address"                          = "none"
    "user.canonical-ai-lab-assistant.owner" = local.env_id
    "user.canonical-ai-lab-assistant.role"  = "ovn-uplink"
  }
}

# These bridges deliberately have no host address or DHCP. The guest receives
# deterministic static addresses through cloud-init, keeping its default route
# on the management NIC.
resource "lxd_network" "ovn_underlay" {
  count = local.segregated_networking ? 1 : 0
  name  = "ca-${var.resource_namespace}-ov"
  type  = "bridge"
  config = {
    "ipv4.address"                          = "none"
    "ipv6.address"                          = "none"
    "user.canonical-ai-lab-assistant.owner" = local.env_id
    "user.canonical-ai-lab-assistant.role"  = "ovn-underlay"
    "user.canonical-ai-lab-assistant.cidr"  = var.microcloud_ovn_underlay_cidr
  }
}

resource "lxd_network" "ceph" {
  count = local.segregated_networking ? 1 : 0
  name  = "ca-${var.resource_namespace}-ce"
  type  = "bridge"
  config = {
    "ipv4.address"                          = "none"
    "ipv6.address"                          = "none"
    "user.canonical-ai-lab-assistant.owner" = local.env_id
    "user.canonical-ai-lab-assistant.role"  = "ceph"
    "user.canonical-ai-lab-assistant.cidr"  = var.microcloud_ceph_network_cidr
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
      node = pair[0] # 0-based node index
      osd  = pair[1] # 0-based OSD index within node
    }
  ]
}

resource "lxd_volume" "microcloud_ceph_disks" {
  count        = length(local.ceph_volumes)
  name         = "${local.lxd_prefix}-ceph-${local.ceph_volumes[count.index].node + 1}-${local.ceph_volumes[count.index].osd + 1}"
  pool         = var.lxd_storage_pool
  content_type = "block"
  description  = "Canonical AI Lab Assistant Ceph OSD for ${local.env_id}"
  config = {
    size                                    = "${var.microcloud_ceph_disk_size_gib}GiB"
    "user.canonical-ai-lab-assistant.owner" = local.env_id
    "user.canonical-ai-lab-assistant.role"  = "ceph-osd"
  }
}

# -----------------------------------------------------------------------
# Local ZFS volumes (one per node, optional — skipped when local_disk_size_gib=0)
# -----------------------------------------------------------------------

resource "lxd_volume" "microcloud_local_disks" {
  count        = var.local_disk_size_gib > 0 ? var.microcloud_node_count : 0
  name         = "${local.lxd_prefix}-local-${count.index + 1}"
  pool         = var.lxd_storage_pool
  content_type = "block"
  description  = "Canonical AI Lab Assistant local disk for ${local.env_id}"
  config = {
    size                                    = "${var.local_disk_size_gib}GiB"
    "user.canonical-ai-lab-assistant.owner" = local.env_id
    "user.canonical-ai-lab-assistant.role"  = "local-disk"
  }
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
    properties = merge(
      { network = lxd_network.ovn_uplink.name },
      local.segregated_networking ? { hwaddr = local.node_macs[count.index]["ovn-uplink"] } : {}
    )
  }

  # Override the profile's management device only in segregated mode so
  # cloud-init can match and rename it deterministically.
  dynamic "device" {
    for_each = local.segregated_networking ? [1] : []
    content {
      name = "eth0"
      type = "nic"
      properties = {
        network = var.lxd_network_name
        hwaddr  = local.node_macs[count.index]["mgmt0"]
      }
    }
  }

  dynamic "device" {
    for_each = local.segregated_networking ? [1] : []
    content {
      name = "eth2"
      type = "nic"
      properties = {
        network = lxd_network.ovn_underlay[0].name
        hwaddr  = local.node_macs[count.index]["ovn-underlay"]
      }
    }
  }

  dynamic "device" {
    for_each = local.segregated_networking ? [1] : []
    content {
      name = "eth3"
      type = "nic"
      properties = {
        network = lxd_network.ceph[0].name
        hwaddr  = local.node_macs[count.index]["ceph-general"]
      }
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

  config = merge({
    "user.user-data"                        = <<-EOT
      #cloud-config
      ssh_authorized_keys:
        - ${var.ssh_public_key}
    EOT
    "user.canonical-ai-lab-assistant.owner" = local.env_id
    "user.canonical-ai-lab-assistant.role"  = "microcloud-node"
    }, local.segregated_networking ? {
    "cloud-init.network-config" = yamlencode({
      version = 2
      ethernets = {
        mgmt0 = {
          match    = { macaddress = local.node_macs[count.index]["mgmt0"] }
          set-name = "mgmt0"
          dhcp4    = true
          dhcp6    = false
        }
        ovn-uplink = {
          match      = { macaddress = local.node_macs[count.index]["ovn-uplink"] }
          set-name   = "ovn-uplink"
          dhcp4      = false
          dhcp6      = false
          accept-ra  = false
          link-local = []
          optional   = true
        }
        ovn-underlay = {
          match      = { macaddress = local.node_macs[count.index]["ovn-underlay"] }
          set-name   = "ovn-underlay"
          dhcp4      = false
          dhcp6      = false
          accept-ra  = false
          link-local = []
          addresses = [
            "${cidrhost(var.microcloud_ovn_underlay_cidr, count.index + 10)}/${split("/", var.microcloud_ovn_underlay_cidr)[1]}"
          ]
        }
        ceph-general = {
          match      = { macaddress = local.node_macs[count.index]["ceph-general"] }
          set-name   = "ceph-general"
          dhcp4      = false
          dhcp6      = false
          accept-ra  = false
          link-local = []
          addresses = [
            "${cidrhost(var.microcloud_ceph_network_cidr, count.index + 10)}/${split("/", var.microcloud_ceph_network_cidr)[1]}"
          ]
        }
      }
    })
  } : {})
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
            name => {
              ansible_connection           = "lxd"
              expected_ceph_disks          = var.ceph_disks_per_node
              local_disk_enabled           = var.local_disk_size_gib > 0
              microcloud_network_mode      = var.microcloud_network_mode
              microcloud_ovn_underlay_ip   = local.segregated_networking ? cidrhost(var.microcloud_ovn_underlay_cidr, index(lxd_instance.microcloud_nodes[*].name, name) + 10) : ""
              microcloud_ovn_underlay_cidr = var.microcloud_ovn_underlay_cidr
              microcloud_ceph_network_ip   = local.segregated_networking ? cidrhost(var.microcloud_ceph_network_cidr, index(lxd_instance.microcloud_nodes[*].name, name) + 10) : ""
              microcloud_ceph_network_cidr = var.microcloud_ceph_network_cidr
            }
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

output "ovn_underlay_network" {
  value       = local.segregated_networking ? lxd_network.ovn_underlay[0].name : null
  description = "Dedicated OVN underlay bridge, when fully segregated networking is enabled"
}

output "ceph_network" {
  value       = local.segregated_networking ? lxd_network.ceph[0].name : null
  description = "Dedicated Ceph bridge, when fully segregated networking is enabled"
}

output "inventory_file" {
  value       = local_file.ansible_inventory.filename
  description = "Path to the generated Ansible inventory file"
}

output "deployment_spec" {
  description = "Resolved environment geometry reused by safe lifecycle operations"
  value = {
    version             = 3
    resource_namespace  = var.resource_namespace
    user_prefix         = var.user_prefix
    ubuntu_image        = var.ubuntu_image
    lxd_network_name    = var.lxd_network_name
    lxd_storage_pool    = var.lxd_storage_pool
    ssh_public_key      = var.ssh_public_key
    node_count          = var.microcloud_node_count
    node_cpu            = var.microcloud_node_cpu
    node_memory_mb      = var.microcloud_node_memory_mb
    root_disk_gib       = var.microcloud_root_disk_size_gib
    ceph_disk_gib       = var.microcloud_ceph_disk_size_gib
    ceph_disks_per_node = var.ceph_disks_per_node
    local_disk_gib      = var.local_disk_size_gib
    network_mode        = var.microcloud_network_mode
    ovn_underlay_cidr   = var.microcloud_ovn_underlay_cidr
    ceph_network_cidr   = var.microcloud_ceph_network_cidr
  }
}
