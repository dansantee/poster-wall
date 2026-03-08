[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('ssh', 'pull', 'restart', 'deploy', 'direct-deploy', 'reboot')]
  [string]$Action,

  [string]$HostName,
  [string]$UserName,
  [string]$RepoPath,
  [string[]]$Path
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$secretsPath = Join-Path $repoRoot 'SECRETS.md'

function Get-SecretValue {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Label
  )

  if (-not (Test-Path $secretsPath)) {
    return $null
  }

  $pattern = '^- ' + [regex]::Escape($Label) + ':\s*`(.+)`$'
  foreach ($line in Get-Content $secretsPath) {
    if ($line -match $pattern) {
      return $matches[1]
    }
  }

  return $null
}

Import-Module Posh-SSH

if (-not $HostName) {
  $HostName = Get-SecretValue 'Host'
}
if (-not $UserName) {
  $UserName = Get-SecretValue 'SSH user'
}
if (-not $RepoPath) {
  $RepoPath = Get-SecretValue 'Repo path'
}

$password = Get-SecretValue 'SSH password'

if (-not $HostName) {
  $HostName = 'poster-wall'
}
if (-not $UserName) {
  $UserName = 'dan'
}
if (-not $RepoPath) {
  $RepoPath = '/home/dan/poster-wall'
}

if (-not $password) {
  throw "SSH password not found in $secretsPath"
}

$securePassword = ConvertTo-SecureString $password -AsPlainText -Force
$credential = [pscredential]::new($UserName, $securePassword)
$session = $null
$sftpSession = $null

function Invoke-PosterWallCommand {
  param(
    [Parameter(Mandatory = $true)]
    [string]$RemoteCommand,

    [switch]$AllowDisconnect
  )

  $result = Invoke-SSHCommand -SSHSession $session -Command $RemoteCommand -TimeOut 60
  if ($result.Output) {
    $result.Output
  }
  if ($result.Error -and $result.ExitStatus -ne 0) {
    $result.Error | Write-Error
  }
  if (-not $AllowDisconnect -and $result.ExitStatus -ne 0) {
    throw "Remote command failed with exit status $($result.ExitStatus)"
  }
}

function Get-DirectDeployPaths {
  if ($Path -and $Path.Count -gt 0) {
    return $Path
  }

  $tracked = git -C $repoRoot diff --name-only
  $untracked = git -C $repoRoot ls-files --others --exclude-standard
  $paths = @($tracked + $untracked | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique)
  return $paths
}

function Convert-ToRemotePath {
  param(
    [Parameter(Mandatory = $true)]
    [string]$RelativePath
  )

  return ($RepoPath.TrimEnd('/') + '/' + ($RelativePath -replace '\\', '/'))
}

function Invoke-DirectDeploy {
  $paths = @(Get-DirectDeployPaths)
  if ($paths.Count -eq 0) {
    throw "No local files selected for direct deploy."
  }

  $sftpSession = New-SFTPSession -ComputerName $HostName -Credential $credential -AcceptKey -ConnectionTimeout 10
  foreach ($relativePath in $paths) {
    $localPath = Join-Path $repoRoot $relativePath
    if (-not (Test-Path $localPath -PathType Leaf)) {
      throw "Direct deploy only supports existing files. Missing or non-file path: $relativePath"
    }

    $remotePath = Convert-ToRemotePath $relativePath
    $remoteDir = Split-Path $remotePath -Parent
    Invoke-PosterWallCommand "mkdir -p '$remoteDir'" | Out-Null
    Set-SFTPItem -SFTPSession $sftpSession -Path $localPath -Destination $remoteDir -Force | Out-Null
    Write-Host "Uploaded $relativePath"
  }

  Write-Warning "Direct deploy updates remote files without changing the Pi's git commit. Use normal deploy after pushing to realign with origin/main."
  Invoke-PosterWallCommand "systemctl --user restart poster-proxy.service poster-web.service poster-kiosk.service && systemctl --user is-active poster-proxy.service poster-web.service poster-kiosk.service"
}

try {
  $session = New-SSHSession -ComputerName $HostName -Credential $credential -AcceptKey -ConnectionTimeout 10

  switch ($Action) {
    'ssh' {
      Write-Host "Connected to $UserName@$HostName. Type 'exit' to close."
      while ($true) {
        $command = Read-Host 'remote'
        if ([string]::IsNullOrWhiteSpace($command)) {
          continue
        }
        if ($command -eq 'exit') {
          break
        }
        Invoke-PosterWallCommand $command
      }
    }

    'pull' {
      Invoke-PosterWallCommand "cd '$RepoPath' && git pull --ff-only"
    }

    'restart' {
      Invoke-PosterWallCommand "systemctl --user restart poster-proxy.service poster-web.service poster-kiosk.service"
    }

    'deploy' {
      Invoke-PosterWallCommand "cd '$RepoPath' && git pull --ff-only && systemctl --user restart poster-proxy.service poster-web.service poster-kiosk.service"
    }

    'direct-deploy' {
      Invoke-DirectDeploy
    }

    'reboot' {
      Invoke-PosterWallCommand "sudo reboot" -AllowDisconnect
    }
  }
}
finally {
  if ($sftpSession) {
    Remove-SFTPSession -SFTPSession $sftpSession | Out-Null
  }
  if ($session) {
    Remove-SSHSession -SSHSession $session | Out-Null
  }
}
