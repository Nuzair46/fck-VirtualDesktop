using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Globalization;
using System.Collections.Generic;
using System.Security.Cryptography;
using System.Web.Script.Serialization;
using dnlib.DotNet;
using dnlib.DotNet.Emit;

// Metadata-only reader: the supplied assembly is never loaded or executed.
public static class StreamerInspector {
    public const string Normalization = "vd-streamer-il-context-v1";
    public sealed class Profile { public int schema_version; public string normalization; public string profile_id; public List<Rule> methods; }
    public sealed class Rule { public string id; public string signature; public string semantic_sha256; public Dictionary<string,int> sites; }
    static string Hash(byte[] bytes) { using(var h=SHA256.Create()) return BitConverter.ToString(h.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant(); }
    static string TypeId(ITypeDefOrRef type) { return type==null ? "" : (type.DefinitionAssembly==null ? "" : type.DefinitionAssembly.Name.String)+"|"+type.FullName; }
    static string Operand(object value,Dictionary<Instruction,int> positions) {
        if(value==null)return "";
        var branch=value as Instruction;if(branch!=null)return "target:"+positions[branch];
        var branches=value as IList<Instruction>;if(branches!=null)return "targets:"+String.Join(",",branches.Select(x=>positions[x].ToString(CultureInfo.InvariantCulture)).ToArray());
        var text=value as string;if(text!=null)return "string-sha256:"+Hash(Encoding.UTF8.GetBytes(text));
        var method=value as IMethod;if(method!=null)return "method:"+TypeId(method.DeclaringType)+"|"+method.FullName;
        var field=value as IField;if(field!=null)return "field:"+TypeId(field.DeclaringType)+"|"+field.FullName;
        var type=value as ITypeDefOrRef;if(type!=null)return "type:"+TypeId(type);
        if(value is float)return "float-bits:"+BitConverter.ToString(BitConverter.GetBytes((float)value));
        if(value is double)return "double-bits:"+BitConverter.ToString(BitConverter.GetBytes((double)value));
        if(value is IFormattable)return value.GetType().Name+":"+((IFormattable)value).ToString(null,CultureInfo.InvariantCulture);
        return value.GetType().Name+":"+value.ToString();
    }
    static int Position(Instruction i,Dictionary<Instruction,int> p,int end) { return i==null?end:p[i]; }
    static string Fingerprint(MethodDef m) {
        var b=m.Body;var p=new Dictionary<Instruction,int>();
        for(int i=0;i<b.Instructions.Count;i++)p.Add(b.Instructions[i],i);
        var instructions=new List<string[]>();
        foreach(var i in b.Instructions){
            var code=i.OpCode.Code.ToString();string operand;
            if(i.IsLdcI4()){code="Ldc_I4";operand=i.GetLdcI4Value().ToString(CultureInfo.InvariantCulture);}
            else{
                var parameter=i.GetParameter(m.Parameters);var local=i.GetLocal(b.Variables);
                if(parameter!=null){code=code.StartsWith("Ldarga")?"Ldarga":code.StartsWith("Starg")?"Starg":"Ldarg";operand="argument:"+parameter.Index;}
                else if(local!=null){code=code.StartsWith("Ldloca")?"Ldloca":code.StartsWith("Stloc")?"Stloc":"Ldloc";operand="local:"+local.Index;}
                else{if(i.Operand is Instruction && code.EndsWith("_S"))code=code.Substring(0,code.Length-2);operand=Operand(i.Operand,p);}
            }
            instructions.Add(new[]{code,operand});
        }
        var handlers=b.ExceptionHandlers.Select(h=>new{kind=h.HandlerType.ToString(),try_start=Position(h.TryStart,p,instructions.Count),try_end=Position(h.TryEnd,p,instructions.Count),handler_start=Position(h.HandlerStart,p,instructions.Count),handler_end=Position(h.HandlerEnd,p,instructions.Count),filter_start=Position(h.FilterStart,p,instructions.Count),catch_type=TypeId(h.CatchType)}).ToArray();
        var canonical=new{signature=(m.IsStatic?"static ":"instance ")+m.FullName,calling_convention=m.MethodSig.CallingConvention.ToString(),generic_parameters=m.GenericParameters.Count,init_locals=b.InitLocals,locals=b.Variables.Select(x=>x.Type.FullName).ToArray(),instructions=instructions,exception_handlers=handlers};
        return Hash(Encoding.UTF8.GetBytes(new JavaScriptSerializer().Serialize(canonical)));
    }
    public static string Inspect(string input,string profilePath,bool capture) {
        var json=new JavaScriptSerializer{MaxJsonLength=16*1024*1024};
        var profile=json.Deserialize<Profile>(File.ReadAllText(profilePath));
        if(profile.schema_version!=2 || profile.normalization!=Normalization || profile.profile_id!="vd-streamer-local-v2")throw new InvalidDataException("Unsupported Streamer profile");
        if(profile.methods==null || profile.methods.Count!=8 || profile.methods.Select(x=>x.id).Distinct().Count()!=8)throw new InvalidDataException("Incomplete or duplicate Streamer profile");
        var bytes=File.ReadAllBytes(input);
        using(var module=ModuleDefMD.Load(bytes)){
            if(module.Name!="VirtualDesktop.Streamer.exe" || module.Assembly==null || module.Assembly.Name!="VirtualDesktop.Streamer")throw new InvalidDataException("Unexpected assembly identity");
            var methods=module.GetTypes().SelectMany(x=>x.Methods).ToArray();
            var result=new List<object>();
            foreach(var rule in profile.methods){
                var candidates=methods.Where(x=>x.FullName==rule.signature).ToArray();
                if(candidates.Length!=1 || !candidates[0].HasBody)throw new InvalidDataException("Required method is missing or ambiguous: "+rule.id);
                var m=candidates[0];var fingerprint=Fingerprint(m);
                if(!capture && fingerprint!=rule.semantic_sha256)throw new InvalidDataException("Contextual IL signature mismatch: "+rule.id);
                var sites=new Dictionary<string,object>();
                foreach(var site in rule.sites){
                    int index=capture ? m.Body.Instructions.ToList().FindIndex(x=>x.Offset==(uint)site.Value) : site.Value;
                    if(index<0 || index>=m.Body.Instructions.Count)throw new InvalidDataException("Invalid instruction anchor: "+rule.id+"/"+site.Key);
                    var i=m.Body.Instructions[index];var metadata=i.Operand as IMDTokenProvider;
                    sites.Add(site.Key,new{index=index,offset=i.Offset,size=i.GetSize(),opcode=i.OpCode.Name,token=metadata==null?0:metadata.MDToken.Raw,operand=i.Operand==null?null:i.Operand.ToString()});
                }
                result.Add(new{id=rule.id,signature=m.FullName,semantic_sha256=fingerprint,token=m.MDToken.Raw,rva=(uint)m.RVA,code_size=m.Body.Instructions.Sum(x=>x.GetSize()),instruction_count=m.Body.Instructions.Count,sites=sites});
            }
            return json.Serialize(new{schema_version=2,profile_id=profile.profile_id,normalization=Normalization,version=System.Diagnostics.FileVersionInfo.GetVersionInfo(input).FileVersion,assembly_version=module.Assembly.Version.ToString(),source_sha256=Hash(bytes),source_size=bytes.Length,methods=result,compatible=!capture,runtime_tested=false});
        }
    }
}
