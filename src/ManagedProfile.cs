using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Globalization;
using System.Collections.Generic;
using System.Security.Cryptography;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;
using dnlib.DotNet;
using dnlib.DotNet.Emit;

internal sealed class ManagedProfileDocument
{
    public int schema_version { get; set; }
    public string profile_id { get; set; }
    public string baseline_description { get; set; }
    public string normalization { get; set; }
    public List<ManagedAssemblyProfile> assemblies { get; set; }
}
internal sealed class ManagedAssemblyProfile
{
    public string file { get; set; }
    public bool modified { get; set; }
    public List<ManagedMethodProfile> methods { get; set; }
    public List<ManagedFieldProfile> fields { get; set; }
}
internal sealed class ManagedMethodProfile
{
    public string id { get; set; }
    public string signature { get; set; }
    public string semantic_sha256 { get; set; }
    public int instruction_count { get; set; }
    public List<ManagedAnchor> anchors { get; set; }
}
internal sealed class ManagedFieldProfile
{
    public string declaring_type { get; set; }
    public string name { get; set; }
    public string value { get; set; }
}
internal sealed class ManagedAnchor
{
    public string kind { get; set; }
    public string value { get; set; }
    public int count { get; set; }
}
internal sealed class ManagedProfileMatches
{
    public ManagedProfileDocument Profile;
    public Dictionary<string,ModuleDefMD> Modules = new Dictionary<string,ModuleDefMD>();
    public Dictionary<string,MethodDef> Methods = new Dictionary<string,MethodDef>();
    public Dictionary<string,string> InputHashes = new Dictionary<string,string>();
    public List<object> Findings = new List<object>();
    public void Dispose() { foreach(var m in Modules.Values) m.Dispose(); }
}

internal static class ManagedProfile
{
    internal const string Normalization = "dnlib-il-semantics-v1";
    static void Check(bool ok,string message) { if(!ok) throw new InvalidDataException(message); }
    internal static string Hash(byte[] data) { using(var hash=SHA256.Create()) return BitConverter.ToString(hash.ComputeHash(data)).Replace("-", "").ToLowerInvariant(); }
    internal static string FileHash(string path) { return Hash(File.ReadAllBytes(path)); }
    // Compiler-generated numbering is not a method identity. Preserve the named
    // source method and suffix role, while omitting generated ordinal numbers.
    internal static string NormalizeName(string name) {
        name=Regex.Replace(name,@"(?<=d__)\d+", "#");
        name=Regex.Replace(name,@"(?<=DisplayClass)\d+_(\d+)", "#_$1");
        name=Regex.Replace(name,@"(?<=b__)\d+_(\d+)", "#_$1");
        return name;
    }
    internal static string Signature(MethodDef m) {
        return (m.IsStatic ? "static " : "instance ")+NormalizeName(m.FullName);
    }
    static string TypeIdentity(ITypeDefOrRef type) {
        if(type==null) return "";
        var assembly=type.DefinitionAssembly;
        return (assembly==null ? "" : assembly.Name.String)+"|"+NormalizeName(type.FullName);
    }
    static string Operand(object value,Dictionary<Instruction,int> indices) {
        if(value==null) return "";
        var branch=value as Instruction;
        if(branch!=null) return "target:"+indices[branch];
        var targets=value as IList<Instruction>;
        if(targets!=null) return "targets:"+String.Join(",",targets.Select(x=>indices[x].ToString(CultureInfo.InvariantCulture)).ToArray());
        var text=value as string;
        if(text!=null) return "string-sha256:"+Hash(Encoding.UTF8.GetBytes(text));
        var method=value as IMethod;
        if(method!=null) return "method:"+TypeIdentity(method.DeclaringType)+"|"+NormalizeName(method.FullName);
        var field=value as IField;
        if(field!=null) return "field:"+TypeIdentity(field.DeclaringType)+"|"+NormalizeName(field.FullName);
        var type=value as ITypeDefOrRef;
        if(type!=null) return "type:"+TypeIdentity(type);
        if(value is float) return "float-bits:"+BitConverter.ToString(BitConverter.GetBytes((float)value));
        if(value is double) return "double-bits:"+BitConverter.ToString(BitConverter.GetBytes((double)value));
        if(value is IFormattable) return value.GetType().Name+":"+((IFormattable)value).ToString(null,CultureInfo.InvariantCulture);
        return value.GetType().Name+":"+NormalizeName(value.ToString());
    }
    static int Position(Instruction i,Dictionary<Instruction,int> indices,int length) { return i==null ? length : indices[i]; }
    internal static string Fingerprint(MethodDef method) {
        Check(method.HasBody,"Required method has no IL body: "+method.FullName);
        var body=method.Body;
        var positions=new Dictionary<Instruction,int>();
        for(int i=0;i<body.Instructions.Count;i++) positions.Add(body.Instructions[i],i);
        var instructions=new List<string[]>();
        foreach(var ins in body.Instructions) {
            string code=ins.OpCode.Code.ToString(),operand;
            if(ins.IsLdcI4()) { code="Ldc_I4"; operand=ins.GetLdcI4Value().ToString(CultureInfo.InvariantCulture); }
            else {
                var parameter=ins.GetParameter(method.Parameters);
                var local=ins.GetLocal(body.Variables);
                if(parameter!=null) {
                    code=code.StartsWith("Ldarga",StringComparison.Ordinal) ? "Ldarga" : code.StartsWith("Starg",StringComparison.Ordinal) ? "Starg" : "Ldarg";
                    operand="argument:"+parameter.Index;
                } else if(local!=null) {
                    code=code.StartsWith("Ldloca",StringComparison.Ordinal) ? "Ldloca" : code.StartsWith("Stloc",StringComparison.Ordinal) ? "Stloc" : "Ldloc";
                    operand="local:"+local.Index;
                } else {
                    if(ins.Operand is Instruction && code.EndsWith("_S",StringComparison.Ordinal)) code=code.Substring(0,code.Length-2);
                    operand=Operand(ins.Operand,positions);
                }
            }
            instructions.Add(new [] {code,operand});
        }
        var handlers=new List<object>();
        foreach(var h in body.ExceptionHandlers) handlers.Add(new {
            kind=h.HandlerType.ToString(),
            try_start=Position(h.TryStart,positions,instructions.Count),try_end=Position(h.TryEnd,positions,instructions.Count),
            handler_start=Position(h.HandlerStart,positions,instructions.Count),handler_end=Position(h.HandlerEnd,positions,instructions.Count),
            filter_start=Position(h.FilterStart,positions,instructions.Count),catch_type=TypeIdentity(h.CatchType)
        });
        var canonical=new {signature=Signature(method),calling_convention=method.MethodSig.CallingConvention.ToString(),
            generic_parameters=method.GenericParameters.Count,init_locals=body.InitLocals,
            locals=body.Variables.Select(x=>NormalizeName(x.Type.FullName)).ToArray(),instructions=instructions,exception_handlers=handlers};
        return Hash(Encoding.UTF8.GetBytes(new JavaScriptSerializer().Serialize(canonical)));
    }
    static int CountAnchor(MethodDef method,ManagedAnchor anchor) {
        int count=0;
        foreach(var ins in method.Body.Instructions) {
            if(anchor.kind=="int32" && ins.IsLdcI4() && ins.GetLdcI4Value().ToString(CultureInfo.InvariantCulture)==anchor.value) count++;
            else if(anchor.kind=="string_sha256" && ins.OpCode==OpCodes.Ldstr && Hash(Encoding.UTF8.GetBytes((string)ins.Operand))==anchor.value) count++;
            else if(anchor.kind=="call") {
                var target=ins.Operand as IMethod;
                if(target!=null && (ins.OpCode==OpCodes.Call || ins.OpCode==OpCodes.Callvirt || ins.OpCode==OpCodes.Newobj) && NormalizeName(target.FullName).Contains(anchor.value)) count++;
            } else if(anchor.kind=="field") {
                var target=ins.Operand as IField;
                if(target!=null && NormalizeName(target.FullName)==anchor.value) count++;
            }
        }
        return count;
    }
    internal static ManagedProfileMatches Validate(string input,string profilePath,ModuleContext context) {
        var profile=new JavaScriptSerializer().Deserialize<ManagedProfileDocument>(File.ReadAllText(profilePath));
        Check(profile!=null && profile.schema_version==1 && profile.normalization==Normalization,"Unsupported managed patch profile");
        Check(profile.profile_id=="vd-quest-local-pairing-v1","Unsupported managed patch recipe");
        Check(profile.assemblies!=null && profile.assemblies.Count==4,"Incomplete managed patch profile");
        string[] expectedFiles={"VirtualDesktop.Android.dll","VirtualDesktop.Mobile.dll","VirtualDesktop.Net.dll","VirtualDesktop.Interfaces.dll"};
        Check(profile.assemblies.Select(a=>a.file).OrderBy(x=>x).SequenceEqual(expectedFiles.OrderBy(x=>x)),"Profile assembly set differs from this patch recipe");
        var result=new ManagedProfileMatches {Profile=profile};
        try {
            foreach(var ap in profile.assemblies) {
                Check(Path.GetFileName(ap.file)==ap.file && ap.file.EndsWith(".dll",StringComparison.Ordinal),"Invalid profile assembly path");
                Check(ap.modified==(ap.file=="VirtualDesktop.Android.dll"||ap.file=="VirtualDesktop.Mobile.dll"),"Incorrect profile modification role");
                string source=Path.Combine(input,ap.file);
                var module=ModuleDefMD.Load(source,context);
                result.Modules.Add(ap.file,module);
                result.InputHashes.Add(ap.file,FileHash(source));
                foreach(var expected in ap.methods) {
                    var candidates=module.GetTypes().SelectMany(t=>t.Methods).Where(m=>Signature(m)==expected.signature).ToArray();
                    Check(candidates.Length==1,"Expected one signature match for "+expected.id+"; found "+candidates.Length);
                    var candidate=candidates[0];
                    string actual=Fingerprint(candidate);
                    Check(actual==expected.semantic_sha256,"Required method behavior differs from profile: "+expected.id);
                    Check(candidate.Body.Instructions.Count==expected.instruction_count,"Instruction count differs: "+expected.id);
                    foreach(var anchor in expected.anchors) {
                        Check(anchor.kind=="int32" || anchor.kind=="string_sha256" || anchor.kind=="call" || anchor.kind=="field","Unknown profile anchor kind");
                        Check(CountAnchor(candidate,anchor)==anchor.count,"Required pattern count differs in "+expected.id+": "+anchor.kind);
                    }
                    result.Methods.Add(expected.id,candidate);
                    result.Findings.Add(new {id=expected.id,file=ap.file,method=candidate.FullName,
                        token=candidate.MDToken.Raw.ToString("x8"),semantic_sha256=actual,anchors_verified=expected.anchors.Count});
                }
                foreach(var field in ap.fields) {
                    var candidates=module.GetTypes().Where(t=>t.FullName==field.declaring_type).SelectMany(t=>t.Fields).Where(f=>f.Name==field.name).ToArray();
                    Check(candidates.Length==1 && candidates[0].HasConstant && Convert.ToString(candidates[0].Constant.Value,CultureInfo.InvariantCulture)==field.value,
                        "Required enum/constant field differs: "+field.declaring_type+"::"+field.name);
                }
            }
            string[] required={"android.create_token","android.get_tokens","android.startup","android.create_token_async","android.get_tokens_async",
                "mobile.identity_getter","mobile.signature_getter","mobile.identity_async","mobile.identity_check","mobile.input_constructor",
                "mobile.keyboard_constructor","mobile.discovery_async","net.discovery_message","interfaces.token_constructor","interfaces.token_constructor_userid"};
            Check(required.All(id=>result.Methods.ContainsKey(id)),"Profile is missing a required recipe method");
            Check(profile.assemblies.Single(a=>a.file=="VirtualDesktop.Interfaces.dll").fields.Any(f=>
                f.declaring_type=="VirtualDesktop.Interfaces.Platform"&&f.name=="OculusQuest"&&f.value=="1"),"Profile must validate the local Quest protocol value");
            return result;
        } catch {result.Dispose(); throw;}
    }
    static ManagedMethodProfile Seed(ModuleDefMD module,string id,Func<MethodDef,bool> select,params ManagedAnchor[] anchors) {
        var method=module.GetTypes().SelectMany(t=>t.Methods).Single(select);
        foreach(var anchor in anchors) {anchor.count=CountAnchor(method,anchor);Check(anchor.count>0,"Baseline anchor not found: "+id+" "+anchor.value);}
        return new ManagedMethodProfile {id=id,signature=Signature(method),semantic_sha256=Fingerprint(method),instruction_count=method.Body.Instructions.Count,anchors=anchors.ToList()};
    }
    static ManagedAnchor Call(string value) {return new ManagedAnchor {kind="call",value=value};}
    static ManagedAnchor Integer(int value) {return new ManagedAnchor {kind="int32",value=value.ToString(CultureInfo.InvariantCulture)};}
    internal static void GenerateBaseline(string input,string output) {
        // Profile generation is a maintainer operation against this reviewed
        // baseline, not an automatic acceptance path for unknown code.
        var baseline=new Dictionary<string,string> {
            {"VirtualDesktop.Android.dll","925041c62546c6843a8b94317885ff03cb14e8b1d0c9d87b79c505d9f19b9f9a"},
            {"VirtualDesktop.Mobile.dll","33f8189b421bfcc698a3c1bba3f2f6e1e9218e6984c7da99fa6fae6fa5ecf58e"},
            {"VirtualDesktop.Net.dll","90b9ed336c45be38ee5c6cb9e04f13eef7e1634b6b590f6fda4b96f80d3f6977"},
            {"VirtualDesktop.Interfaces.dll","cee05fe5f0ec68392d1825232ec53adbd26f31c09696ce4512c4a492d6b82239"}
        };
        Check(!File.Exists(output),"Refusing to overwrite a reviewed profile");
        var profile=new ManagedProfileDocument {schema_version=1,profile_id="vd-quest-local-pairing-v1",baseline_description="Virtual Desktop Quest 1.34.25.0; version is descriptive, not an input gate",normalization=Normalization,assemblies=new List<ManagedAssemblyProfile>()};
        foreach(var pair in baseline) {
            Check(FileHash(Path.Combine(input,pair.Key))==pair.Value,"Profile generator requires the reviewed baseline: "+pair.Key);
            using(var m=ModuleDefMD.Load(Path.Combine(input,pair.Key))) {
                var a=new ManagedAssemblyProfile {file=pair.Key,modified=pair.Key=="VirtualDesktop.Android.dll"||pair.Key=="VirtualDesktop.Mobile.dll",methods=new List<ManagedMethodProfile>(),fields=new List<ManagedFieldProfile>()};
                if(pair.Key=="VirtualDesktop.Android.dll") {
                    a.methods.Add(Seed(m,"android.create_token",x=>x.DeclaringType.FullName=="VirtualDesktop.VrApp"&&x.Name=="CreateAccessTokenAsync"));
                    a.methods.Add(Seed(m,"android.get_tokens",x=>x.DeclaringType.FullName=="VirtualDesktop.VrApp"&&x.Name=="GetAccessTokenAsync"));
                    a.methods.Add(Seed(m,"android.startup",x=>x.DeclaringType.FullName=="VirtualDesktop.VrApp"&&x.Name=="OnCreate",Call("Android.Content.PM.Signature::ToCharsString")));
                    a.methods.Add(Seed(m,"android.create_token_async",x=>x.DeclaringType.FullName.Contains("/<CreateAccessTokenAsync>d__")&&x.Name=="MoveNext",Call("Oculus.Platform.OVRPlatform::InitializeAndroidAsync"),Call("Oculus.Platform.OVRPlatform::IsViewerEntitledAsync"),Call("Oculus.Platform.OVRPlatform::GetLoggedInUserAsync")));
                    a.methods.Add(Seed(m,"android.get_tokens_async",x=>x.DeclaringType.FullName.Contains("/<GetAccessTokenAsync>d__")&&x.Name=="MoveNext",Call("Oculus.Platform.OVRPlatform::GetUserProofAsync")));
                } else if(pair.Key=="VirtualDesktop.Mobile.dll") {
                    a.methods.Add(Seed(m,"mobile.identity_getter",x=>x.DeclaringType.FullName=="VirtualDesktop.Mobile.UserSettings"&&x.Name=="get_HasValidIdentity"));
                    a.methods.Add(Seed(m,"mobile.signature_getter",x=>x.DeclaringType.FullName=="VirtualDesktop.Mobile.UserSettings"&&x.Name=="get_Signature"));
                    a.methods.Add(Seed(m,"mobile.identity_async",x=>x.DeclaringType.FullName=="VirtualDesktop.Mobile.UserSettings"&&x.Name=="GetHasValidIdentityAsync"));
                    a.methods.Add(Seed(m,"mobile.identity_check",x=>x.DeclaringType.FullName=="VirtualDesktop.Mobile.UserSettings"&&x.Name.String.StartsWith("<GetHasValidIdentityAsync>"),Integer(1778352230)));
                    a.methods.Add(Seed(m,"mobile.input_constructor",x=>x.DeclaringType.FullName=="VirtualDesktop.Mobile.InputSystem"&&x.IsInstanceConstructor,Integer(1778352252)));
                    a.methods.Add(Seed(m,"mobile.keyboard_constructor",x=>x.DeclaringType.FullName=="VirtualDesktop.Mobile.Keyboard"&&x.IsInstanceConstructor,Integer(-1386013078)));
                    a.methods.Add(Seed(m,"mobile.discovery_async",x=>x.DeclaringType.FullName.Contains("/<GetComputersAsync>d__")&&x.Name=="MoveNext",new ManagedAnchor {kind="string_sha256",value=Hash(Encoding.UTF8.GetBytes("Meta servers unreachable, only showing local computers"))}));
                } else if(pair.Key=="VirtualDesktop.Net.dll") {
                    a.methods.Add(Seed(m,"net.discovery_message",x=>x.DeclaringType.FullName=="VirtualDesktop.Net.ComputerDiscoveryClient"&&x.Name=="CreateBroadcastMessage"));
                } else {
                    a.methods.Add(Seed(m,"interfaces.token_constructor",x=>x.DeclaringType.FullName=="VirtualDesktop.Interfaces.PlatformAccessToken"&&x.IsInstanceConstructor&&x.MethodSig.Params.Count==2));
                    a.methods.Add(Seed(m,"interfaces.token_constructor_userid",x=>x.DeclaringType.FullName=="VirtualDesktop.Interfaces.PlatformAccessToken"&&x.IsInstanceConstructor&&x.MethodSig.Params.Count==3&&x.MethodSig.Params[2].FullName=="System.UInt64"));
                    a.fields.Add(new ManagedFieldProfile {declaring_type="VirtualDesktop.Interfaces.Platform",name="OculusQuest",value="1"});
                }
                profile.assemblies.Add(a);
            }
        }
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(output)));
        File.WriteAllText(output,new JavaScriptSerializer().Serialize(profile));
    }
}
