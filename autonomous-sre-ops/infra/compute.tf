resource "azurerm_network_interface" "target" {
  for_each            = local.targets
  name                = "nic-${each.value.name}"
  location            = var.location
  resource_group_name = azurerm_resource_group.demo[each.key].name
  tags                = local.tags

  ip_configuration {
    name                          = "private"
    subnet_id                     = azurerm_subnet.demo["workloads"].id
    private_ip_address_allocation = "Static"
    private_ip_address            = each.value.address
  }
}

resource "azurerm_linux_virtual_machine" "target" {
  for_each                                               = local.targets
  name                                                   = each.value.name
  location                                               = var.location
  resource_group_name                                    = azurerm_resource_group.demo[each.key].name
  size                                                   = "Standard_D2s_v3"
  admin_username                                         = "demoops"
  disable_password_authentication                        = true
  provision_vm_agent                                     = true
  patch_mode                                             = "AutomaticByPlatform"
  bypass_platform_safety_checks_on_user_schedule_enabled = true
  patch_assessment_mode                                  = "AutomaticByPlatform"
  network_interface_ids                                  = [azurerm_network_interface.target[each.key].id]
  tags                                                   = merge(local.tags, { target-role = each.key })

  admin_ssh_key {
    username   = "demoops"
    public_key = trimspace(var.admin_ssh_public_key)
  }

  identity {
    type = "SystemAssigned"
  }

  os_disk {
    name                 = "osdisk-${each.value.name}"
    caching              = "ReadWrite"
    storage_account_type = "Standard_LRS"
    disk_size_gb         = 32
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "0001-com-ubuntu-server-jammy"
    sku       = "22_04-lts-gen2"
    version   = "22.04.202608060"
  }

  boot_diagnostics {}

  custom_data = base64encode("#cloud-config\n${yamlencode({
    package_update = false
    write_files = [
      {
        path        = "/usr/local/lib/asre-demo/demo_common.py"
        permissions = "0644"
        content     = file("${path.module}/../workloads/linux-demo-service/demo_common.py")
      },
      {
        path        = "/usr/local/lib/asre-demo/workload.py"
        permissions = "0644"
        content     = file("${path.module}/../workloads/linux-demo-service/workload.py")
      },
      {
        path        = "/usr/local/lib/asre-demo/guest_control.py"
        permissions = "0755"
        content     = file("${path.module}/../workloads/linux-demo-service/guest_control.py")
      },
      {
        path        = "/usr/local/lib/asre-demo/patch_failure.py"
        permissions = "0644"
        content     = file("${path.module}/../workloads/linux-demo-service/patch_failure.py")
      },
      {
        path        = "/usr/local/lib/asre-demo/bootstrap.sh"
        permissions = "0755"
        content     = file("${path.module}/../workloads/linux-demo-service/bootstrap.sh")
      },
      {
        path        = "/etc/systemd/system/asre-demo.service"
        permissions = "0644"
        content     = file("${path.module}/../workloads/linux-demo-service/asre-demo.service")
      },
      {
        path        = "/etc/asre-demo/policy.json"
        permissions = "0600"
        content = jsonencode({
          owner           = "asre-demo"
          role            = each.key
          faults_enabled  = false
          max_ttl_seconds = 1800
        })
      },
      {
        path        = "/etc/asre-demo/runner.pub"
        permissions = "0644"
        content     = "${trimspace(var.runner_ssh_public_key)}\n"
      },
      {
        path        = "/etc/sudoers.d/asre-demo"
        permissions = "0440"
        content     = "asrerunner ALL=(root) NOPASSWD: /usr/bin/python3 /usr/local/lib/asre-demo/guest_control.py --inspect-only\n"
      }
    ]
    runcmd = [["/usr/local/lib/asre-demo/bootstrap.sh", each.key]]
  })}")

  lifecycle {
    # Cloud-init runs once; bounded guest updates must not recreate existing VMs.
    ignore_changes = [custom_data]
  }

  depends_on = [
    azurerm_subnet_nat_gateway_association.demo,
    azurerm_nat_gateway_public_ip_association.demo,
    azurerm_subnet_network_security_group_association.demo
  ]
}

resource "azurerm_managed_disk" "disposable" {
  name                 = "disk-asre-disposable-cus"
  location             = var.location
  resource_group_name  = azurerm_resource_group.demo["linux"].name
  storage_account_type = "Standard_LRS"
  create_option        = "Empty"
  disk_size_gb         = 4
  tags                 = merge(local.tags, { purpose = "disposable-demo-files-only" })
}

resource "azurerm_virtual_machine_data_disk_attachment" "disposable" {
  managed_disk_id    = azurerm_managed_disk.disposable.id
  virtual_machine_id = azurerm_linux_virtual_machine.target["linux"].id
  lun                = 0
  caching            = "None"
}

resource "azurerm_virtual_machine_extension" "monitor" {
  for_each                   = local.targets
  name                       = "AzureMonitorLinuxAgent"
  virtual_machine_id         = azurerm_linux_virtual_machine.target[each.key].id
  publisher                  = "Microsoft.Azure.Monitor"
  type                       = "AzureMonitorLinuxAgent"
  type_handler_version       = "1.0"
  automatic_upgrade_enabled  = true
  auto_upgrade_minor_version = true
  tags                       = local.tags
}
