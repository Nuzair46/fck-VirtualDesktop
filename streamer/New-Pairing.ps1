[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Output,
    [ValidatePattern('^[a-z0-9][a-z0-9._-]{2,29}$')][string]$Account
)
$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $Output) { throw 'Pairing output already exists. Reuse it for both patchers; do not replace it silently.' }
if (!$Account) { $Account = 'vdlocal' + [Guid]::NewGuid().ToString('N').Substring(0,8) }
$vdOutputPath = [IO.Path]::GetFullPath($Output)
$vdOutputDirectory = [IO.Path]::GetDirectoryName($vdOutputPath)
if (!(Test-Path -LiteralPath $vdOutputDirectory -PathType Container)) { throw 'Create the output directory first.' }
$vdRsa = New-Object Security.Cryptography.RSACng -ArgumentList 2048
try {
    $vdAccountBytes = [Text.Encoding]::UTF8.GetBytes($Account)
    $vdMessage = New-Object byte[] (1 + $vdAccountBytes.Length)
    $vdMessage[0] = 1
    [Array]::Copy($vdAccountBytes, 0, $vdMessage, 1, $vdAccountBytes.Length)
    $vdSignature = $vdRsa.SignData($vdMessage, [Security.Cryptography.HashAlgorithmName]::SHA256, [Security.Cryptography.RSASignaturePadding]::Pss)
    if (!$vdRsa.VerifyData($vdMessage, $vdSignature, [Security.Cryptography.HashAlgorithmName]::SHA256, [Security.Cryptography.RSASignaturePadding]::Pss)) { throw 'Pairing signature self-test failed.' }
    $vdPublic = $vdRsa.ExportParameters($false)
    if ($vdPublic.Modulus.Length -ne 256 -or [Convert]::ToBase64String($vdPublic.Exponent) -ne 'AQAB' -or $vdSignature.Length -ne 256) { throw 'Unexpected RSA key/signature dimensions.' }
    $vdDocument = [ordered]@{
        schema_version = 1
        account = $Account
        platform = 1
        signature_algorithm = 'RSA-PSS-SHA256'
        signature_base64 = [Convert]::ToBase64String($vdSignature)
        modulus_base64 = [Convert]::ToBase64String($vdPublic.Modulus)
        exponent_base64 = [Convert]::ToBase64String($vdPublic.Exponent)
        public_key_xml = $vdRsa.ToXmlString($false)
        signed_message_base64 = [Convert]::ToBase64String($vdMessage)
        created_utc = [DateTime]::UtcNow.ToString('o')
    }
    $vdJson = $vdDocument | ConvertTo-Json -Depth 4
    $vdUtf8 = New-Object Text.UTF8Encoding -ArgumentList $false
    $vdStream = [IO.File]::Open($vdOutputPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $vdBytes=$vdUtf8.GetBytes($vdJson); $vdStream.Write($vdBytes,0,$vdBytes.Length) } finally { $vdStream.Dispose() }
    [pscustomobject]@{Pairing=$vdOutputPath;Account=$Account;Algorithm='RSA-PSS-SHA256';PrivateKeySaved=$false} | ConvertTo-Json
} finally { $vdRsa.Dispose() }
