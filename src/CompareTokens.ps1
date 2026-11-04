param([string]$Original,[string]$Modified)
$ErrorActionPreference='Stop'
Add-Type -Path (Join-Path $PSScriptRoot '..\tools\Mono.Cecil.dll')
function Get-Types($types){foreach($t in $types){$t; Get-Types $t.NestedTypes}}
function Get-Tokens($path){
 $module=[Mono.Cecil.ModuleDefinition]::ReadModule($path)
 $dict=@{}
 foreach($t in (Get-Types $module.Types)){
  $dict['T:'+$t.FullName]=$t.MetadataToken.ToUInt32()
  foreach($m in $t.Methods){$dict['M:'+$m.FullName]=$m.MetadataToken.ToUInt32()}
  foreach($f in $t.Fields){$dict['F:'+$f.FullName]=$f.MetadataToken.ToUInt32()}
 }
 return $dict
}
$old=Get-Tokens $Original
$new=Get-Tokens $Modified
$diff=foreach($name in $old.Keys){if($old[$name] -ne $new[$name]){[pscustomobject]@{Name=$name;Old=$old[$name];New=$new[$name]}}}
$diff | Select-Object -First 15 | ConvertTo-Json
'Total changes: '+@($diff).Count
