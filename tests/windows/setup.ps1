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

New-Item -ItemType Directory -Force -Path C:\PandoraBox\keys | Out-Null
icacls C:\PandoraBox\keys /grant "kiosk:(OI)(CI)M" | Out-Null

# OpenSSH refuses a key file that others could change
$keys = "C:\ProgramData\ssh\kiosk_authorized_keys"
Copy-Item (Join-Path $source "kiosk.pub") $keys
icacls $keys /inheritance:r /grant "SYSTEM:F" "Administrators:F" "kiosk:R" | Out-Null

Add-Content -Path C:\ProgramData\ssh\sshd_config -Value @"

Match User kiosk
    AuthorizedKeysFile __PROGRAMDATA__/ssh/kiosk_authorized_keys
    ChrootDirectory C:\PandoraBox
    ForceCommand internal-sftp
    AllowTcpForwarding no
    PermitTTY no
"@
Restart-Service sshd

if (-not (Get-NetFirewallRule -Name OpenSSH-Server-In-TCP -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name OpenSSH-Server-In-TCP -DisplayName "OpenSSH Server (sshd)" `
        -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
}

Stop-Transcript
Stop-Computer -Force
