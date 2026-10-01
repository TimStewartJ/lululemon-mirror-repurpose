param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = 'Stop'
$Python = Get-Command python -ErrorAction Stop
& $Python.Source (Join-Path $PSScriptRoot 'validate.py') @Arguments
exit $LASTEXITCODE
