$ErrorActionPreference = 'Stop'
$vdRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$null = [Reflection.Assembly]::LoadFrom((Join-Path $vdRoot 'tools\ilspycmd\tools\net8.0\any\Mono.Cecil.dll'))
$vdModule = [Mono.Cecil.ModuleDefinition]::ReadModule('C:\Program Files\Virtual Desktop Streamer\VirtualDesktop.Streamer.exe')
$vdResults = @()
foreach ($vdType in $vdModule.GetTypes()) {
    if ($vdType.FullName.StartsWith('VirtualDesktop.Streamer.ConnectionManager') -or $vdType.FullName.StartsWith('VirtualDesktop.Streamer.UpdateHelper') -or $vdType.FullName -eq 'VirtualDesktop.Streamer.App' -or $vdType.FullName -eq ([char]20).ToString()+'.'+[char]2 -or $vdType.FullName -eq ([char]132).ToString()+[char]2+'.'+[char]2) {
        foreach ($vdMethod in $vdType.Methods) {
            if (!$vdMethod.HasBody) { continue }
            $vdInstructions = @($vdMethod.Body.Instructions | ForEach-Object {
                $vdOperand = $_.Operand
                $vdOperandToken = if ($vdOperand -is [Mono.Cecil.IMetadataTokenProvider]) { $vdOperand.MetadataToken.ToUInt32() } else { $null }
                $vdTarget = if ($vdOperand -is [Mono.Cecil.Cil.Instruction]) { $vdOperand.Offset } else { $null }
                [pscustomobject]@{ offset=$_.Offset; opcode=$_.OpCode.Name; operand=if($null -ne $vdOperand){$vdOperand.ToString()}else{$null}; target=$vdTarget; token=$vdOperandToken }
            })
            $vdHandlers = @($vdMethod.Body.ExceptionHandlers | ForEach-Object { [pscustomobject]@{ type=$_.HandlerType.ToString();try_start=$_.TryStart.Offset;try_end=$_.TryEnd.Offset;handler_start=$_.HandlerStart.Offset;handler_end=$_.HandlerEnd.Offset } })
            $vdResults += [pscustomobject]@{ name=$vdMethod.FullName; token=$vdMethod.MetadataToken.ToUInt32(); rva=$vdMethod.RVA; size=$vdMethod.Body.CodeSize; instructions=$vdInstructions;handlers=$vdHandlers }
        }
    }
}
$vdResults | ConvertTo-Json -Depth 9 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'evidence\metadata.json') -Encoding UTF8
$vdModule.GetMemberReferences() | Where-Object { $_.DeclaringType.FullName -in @('System.Security.Cryptography.HashAlgorithmName','System.Security.Cryptography.RSASignaturePadding','System.Security.Cryptography.RSA') } | Select-Object FullName,@{Name='token';Expression={$_.MetadataToken.ToUInt32()}} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'evidence\crypto-member-refs.json') -Encoding UTF8
$vdModule.Dispose()
