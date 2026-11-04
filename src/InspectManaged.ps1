param([Parameter(Mandatory=$true)][string]$InputFile,[string]$TypePattern='VrApp|UserSettings|InputSystem|Keyboard',[string]$MethodPattern='CreateAccessTokenAsync|GetAccessTokenAsync|GetHasValidIdentityAsync|get_HasValidIdentity|get_Signature|OnCreate|\.ctor|MoveNext')
$ErrorActionPreference='Stop'
Add-Type -Path (Join-Path $PSScriptRoot '..\..\tools\ilspycmd\tools\net8.0\any\Mono.Cecil.dll')
$m=[Mono.Cecil.ModuleDefinition]::ReadModule($InputFile)
function ShowTypes($types) {
 foreach($t in $types) {
  if($t.FullName -match $TypePattern) {
   foreach($method in $t.Methods) {
    if($method.Name -match $MethodPattern -and $method.HasBody) {
     'METHOD '+$method.MetadataToken+' '+$method.FullName
     foreach($i in $method.Body.Instructions) { $i.ToString() }
    }
   }
  }
  ShowTypes $t.NestedTypes
 }
}
ShowTypes $m.Types
