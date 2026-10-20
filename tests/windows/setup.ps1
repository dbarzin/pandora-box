# OpenSSH server of the PandoraBox tests (tests/windows/windows.py), run once
# at the first logon of Administrator, as docs/airgap.md describes it for a
# real server: a local account "kiosk", not an administrator, its key in
# C:\ProgramData\ssh, restricted to SFTP in C:\PandoraBox. Then shut down:
# the disk is the base image of the tests.
$ErrorActionPreference = "Stop"
Start-Transcript -Path C:\pandora-box-setup.log
$source = (Get-Volume -FileSystemLabel PANDORA).DriveLetter + ":\"

# Built in on Windows Server 2025 (stopped); a capability before it
if (-not (Get-Service sshd -ErrorAction SilentlyContinue)) {
    Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
}
Set-Service -Name sshd -StartupType Automatic
# The first start writes C:\ProgramData\ssh\sshd_config and the host keys
Start-Service sshd

# The account of the kiosks: its password is never used (key only)
$chars = [char[]]"ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
$password = -join (1..24 | ForEach-Object { $chars | Get-Random }) + "!9a"
New-LocalUser -Name kiosk -Password (ConvertTo-SecureString $password -AsPlainText -Force) `
    -PasswordNeverExpires -AccountNeverExpires -Description "PandoraBox kiosks (SFTP)"

# Windows Server 2025: sshd only lets in the administrators and the local
# group "OpenSSH Users" (AllowGroups of its sshd_config)
if (Get-LocalGroup -Name "OpenSSH Users" -ErrorAction SilentlyContinue) {
    Add-LocalGroupMember -Group "OpenSSH Users" -Member kiosk
}

New-Item -ItemType Directory -Force -Path C:\PandoraBox\keys | Out-Null
icacls C:\PandoraBox\keys /grant "kiosk:(OI)(CI)M" | Out-Null

# OpenSSH refuses a key file that others could change
$keys = "C:\ProgramData\ssh\kiosk_authorized_keys"
Copy-Item (Join-Path $source "kiosk.pub") $keys
icacls $keys /inheritance:r /grant "SYSTEM:F" "Administrators:F" "kiosk:R" | Out-Null

# Test machine only: a detailed log of sshd in C:\ProgramData\ssh\logs\sshd.log,
# read from the disk by "tests/windows/windows.sh logs" (global options come
# before the first Match block)
$config = Get-Content C:\ProgramData\ssh\sshd_config
Set-Content -Path C:\ProgramData\ssh\sshd_config -Value (@("SyslogFacility LOCAL0", "LogLevel DEBUG3") + $config)

Add-Content -Path C:\ProgramData\ssh\sshd_config -Value @"

Match User kiosk
    AuthorizedKeysFile __PROGRAMDATA__/ssh/kiosk_authorized_keys
    ChrootDirectory C:\PandoraBox
    ForceCommand internal-sftp
    AllowTcpForwarding no
    PermitTTY no
"@
Restart-Service sshd

# The rule that comes with OpenSSH only allows the Private network profile:
# the network of the virtual machine is not identified, hence Public
if (Get-NetFirewallRule -Name OpenSSH-Server-In-TCP -ErrorAction SilentlyContinue) {
    Set-NetFirewallRule -Name OpenSSH-Server-In-TCP -Enabled True -Profile Any
} else {
    New-NetFirewallRule -Name OpenSSH-Server-In-TCP -DisplayName "OpenSSH Server (sshd)" `
        -Enabled True -Profile Any -Direction Inbound -Protocol TCP -Action Allow `
        -LocalPort 22 | Out-Null
}

# For the record (C:\pandora-box-setup.log)
Get-Service sshd | Format-List Name, Status, StartType
Get-NetFirewallRule -Name OpenSSH-Server-In-TCP | Format-List Name, Enabled, Profile, Action
Get-NetConnectionProfile | Format-List InterfaceAlias, NetworkCategory
Test-NetConnection -ComputerName localhost -Port 22 | Format-List TcpTestSucceeded

Stop-Transcript
Stop-Computer -Force
