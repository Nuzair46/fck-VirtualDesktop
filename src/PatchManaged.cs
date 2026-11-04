// Builds local identity support into the two managed APK assemblies.
// Never loads or executes the target assemblies. dnlib preserves metadata tokens.
using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;
using dnlib.DotNet;
using dnlib.DotNet.Emit;
using dnlib.DotNet.Writer;
using dnlib.DotNet.MD;

internal static class PatchManaged
{
    static readonly string[] ModifiedNames = {"VirtualDesktop.Android.dll", "VirtualDesktop.Mobile.dll"};
    static List<object> Changes = new List<object>();
    static string Hex(byte[] bytes) { return BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant(); }
    static string Hash(string path) { using(var s=File.OpenRead(path)) using(var h=SHA256.Create()) return Hex(h.ComputeHash(s)); }
    static void Require(bool ok, string message) { if(!ok) throw new InvalidDataException(message); }
    static IEnumerable<MethodDef> Methods(ModuleDef m) { return m.GetTypes().SelectMany(t=>t.Methods); }
    static TypeDef Type(ModuleDef m, string name) { return m.GetTypes().Single(t=>t.FullName==name); }
    static MethodDef Method(TypeDef t, string name) { return t.Methods.Single(x=>x.Name==name); }
    static void Changed(MethodDef m, string change) {
        Changes.Add(new {assembly=m.Module.Name.String, method=m.FullName, token=m.MDToken.Raw.ToString("x8"), change=change});
    }
    static IList<Instruction> Replace(MethodDef m) {
        m.Body=new CilBody(); m.Body.InitLocals=false; m.Body.MaxStack=8;
        // The old generated state machine remains in the metadata but is unreachable.
        for(int i=m.CustomAttributes.Count-1;i>=0;i--)
            if(m.CustomAttributes[i].AttributeType.FullName=="System.Runtime.CompilerServices.AsyncStateMachineAttribute")
                m.CustomAttributes.RemoveAt(i);
        return m.Body.Instructions;
    }
    static IMethod FromResult(ModuleDefMD m, TypeSig value, ClassOrValueTypeSig taskGeneric) {
        var task=m.GetTypeRefs().Single(t=>t.FullName=="System.Threading.Tasks.Task");
        var p=new GenericMVar(0);
        var sig=MethodSig.CreateStaticGeneric(1,new GenericInstSig(taskGeneric,p),p);
        var from=new MemberRefUser(m,"FromResult",sig,task);
        return new MethodSpecUser(from,new GenericInstMethodSig(value));
    }
    static void EmitToken(IList<Instruction> il, IMethod ctor, string account) {
        il.Add(Instruction.Create(OpCodes.Ldc_I4_1)); il.Add(Instruction.Create(OpCodes.Ldstr, account)); il.Add(Instruction.Create(OpCodes.Newobj,ctor));
    }
    static void PatchAndroid(ModuleDefMD m,string account,string certHex,ManagedProfileMatches matches) {
        var ctor=m.GetMemberRefs().First(x=>x.IsMethodRef && x.FullName==matches.Methods["interfaces.token_constructor"].FullName);
        var create=matches.Methods["android.create_token"];
        var createTask=(GenericInstSig)create.ReturnType;
        var il=Replace(create); EmitToken(il,ctor,account);
        il.Add(Instruction.Create(OpCodes.Call,FromResult(m,createTask.GenericArguments[0],createTask.GenericType))); il.Add(Instruction.Create(OpCodes.Ret));
        Changed(create,"Return a local Quest account as a completed Task; no Meta platform identity calls.");
        var get=matches.Methods["android.get_tokens"];
        var getTask=(GenericInstSig)get.ReturnType;
        var tuple=(GenericInstSig)getTask.GenericArguments[0];
        var tupleCtor=m.GetMemberRefs().Single(x=>x.IsMethodRef && x.Name==".ctor" &&
            x.DeclaringType.FullName==tuple.FullName && x.MethodSig.Params.Count==2);
        // Discovery caches its broadcast/RSA key by the FIRST token's object identity.
        // OnCreate stores the completed local-token Task before starting networking.
        // Reuse that token and retain the original independent second-token clone.
        var accessTokenTask=create.DeclaringType.Fields.Single(x=>x.Name=="_accessTokenTask" && x.IsStatic &&
            x.FieldSig.Type.FullName==create.ReturnType.FullName);
        var cachedResult=new MemberRefUser(m,"get_Result",MethodSig.CreateInstance(new GenericVar(0)),
            new TypeSpecUser(accessTokenTask.FieldSig.Type));
        var tokenType=createTask.GenericArguments[0].ToTypeDefOrRef();
        var clone=m.GetMemberRefs().Single(x=>x.IsMethodRef && x.Name=="Clone" &&
            x.DeclaringType.FullName==tokenType.FullName && x.MethodSig.Params.Count==0);
        var clearProof=m.GetMemberRefs().Single(x=>x.IsMethodRef && x.Name=="set_UserProof" &&
            x.DeclaringType.FullName==tokenType.FullName && x.MethodSig.Params.Count==1);
        il=Replace(get);
        il.Add(Instruction.Create(OpCodes.Ldsfld,accessTokenTask));
        il.Add(Instruction.Create(OpCodes.Callvirt,cachedResult));
        il.Add(Instruction.Create(OpCodes.Dup));
        il.Add(Instruction.Create(OpCodes.Ldnull));
        il.Add(Instruction.Create(OpCodes.Callvirt,clearProof));
        il.Add(Instruction.Create(OpCodes.Dup));
        il.Add(Instruction.Create(OpCodes.Callvirt,clone));
        il.Add(Instruction.Create(OpCodes.Castclass,tokenType));
        il.Add(Instruction.Create(OpCodes.Newobj,tupleCtor));
        il.Add(Instruction.Create(OpCodes.Call,FromResult(m,tuple,getTask.GenericType))); il.Add(Instruction.Create(OpCodes.Ret));
        Changed(get,"Reuse the cached local first token and clone the second without user proofs; preserve discovery key caching and offline registry behavior.");
        var onCreate=matches.Methods["android.startup"];
        var cert=onCreate.Body.Instructions.Where(i=>i.OpCode==OpCodes.Ldstr &&
            ((string)i.Operand).StartsWith("308203a73082028f")).ToArray();
        Require(cert.Length==1,"Unexpected startup certificate checks"); cert[0].Operand=certHex;
        Changed(onCreate,"Pin APK startup certificate to the user's APK signing certificate.");
    }
    static int ReplaceInt(MethodDef method,int oldValue,int newValue) {
        int n=0;
        foreach(var i in method.Body.Instructions) if(i.OpCode==OpCodes.Ldc_I4 && (int)i.Operand==oldValue) {
            i.Operand=newValue; n++;
        }
        return n;
    }
    static void PatchMobile(ModuleDefMD m,string signature,int javaHash,int certHash,ManagedProfileMatches matches) {
        var settings=Type(m,"VirtualDesktop.Mobile.UserSettings");
        var valid=matches.Methods["mobile.identity_getter"]; var il=Replace(valid);
        il.Add(Instruction.Create(OpCodes.Ldc_I4_1)); il.Add(Instruction.Create(OpCodes.Ret));
        Changed(valid,"Use the embedded local pairing identity.");
        var sig=matches.Methods["mobile.signature_getter"];
        var convert=m.GetTypeRefs().FirstOrDefault(t=>t.FullName=="System.Convert") ??
            new TypeRefUser(m,"System","Convert",m.CorLibTypes.AssemblyRef);
        var decode=new MemberRefUser(m,"FromBase64String",MethodSig.CreateStatic(new SZArraySig(m.CorLibTypes.Byte),m.CorLibTypes.String),convert);
        il=Replace(sig); il.Add(Instruction.Create(OpCodes.Ldstr,signature)); il.Add(Instruction.Create(OpCodes.Call,decode)); il.Add(Instruction.Create(OpCodes.Ret));
        Changed(sig,"Supply the locally signed RSA-PSS identity for LAN discovery.");
        var check=matches.Methods["mobile.identity_check"];
        Require(ReplaceInt(check,1778352230,unchecked(javaHash-22))==1,"Identity certificate constant mismatch");
        int fields=0;
        foreach(var ins in check.Body.Instructions) {
            var f=ins.Operand as IField;
            if(ins.OpCode==OpCodes.Ldfld && f!=null && f.DeclaringType.FullName==settings.FullName &&
               (f.Name=="_hasValidIdentity" || f.Name=="_signature")) {
                ins.OpCode=OpCodes.Call; ins.Operand=f.Name=="_hasValidIdentity" ? valid : sig; fields++;
            }
        }
        Require(fields==2,"Identity field check pattern mismatch");
        Changed(check,"Retain certificate validation; consult embedded identity/signature getters.");
        var input=matches.Methods["mobile.input_constructor"];
        Require(ReplaceInt(input,1778352252,javaHash)==1,"InputSystem certificate constant mismatch");
        Changed(input,"Pin input-system certificate check to the local signer.");
        var keyboard=matches.Methods["mobile.keyboard_constructor"];
        Require(ReplaceInt(keyboard,-1386013078,certHash)==2,"Keyboard certificate constants mismatch");
        Changed(keyboard,"Retain keyboard certificate checks and original valid-certificate timing behavior.");
        // Clarify the intentional local mode using the existing warning branch.
        int warnings=0;
        foreach(var ins in matches.Methods["mobile.discovery_async"].Body.Instructions)
            if(ins.OpCode==OpCodes.Ldstr && (string)ins.Operand=="Meta servers unreachable, only showing local computers") {
                ins.Operand="Offline pairing: showing local computers"; warnings++;
            }
        Require(warnings==1,"Offline UI warning pattern mismatch");
    }
    static Dictionary<string,uint> Tokens(ModuleDef m) {
        var d=new Dictionary<string,uint>();
        foreach(var t in m.GetTypes()) {
            d.Add("T:"+t.FullName,t.MDToken.Raw);
            foreach(var f in t.Fields) d.Add("F:"+f.FullName,f.MDToken.Raw);
            foreach(var method in t.Methods) d.Add("M:"+method.FullName,method.MDToken.Raw);
        }
        return d;
    }
    static Dictionary<uint,string> References(ModuleDefMD m) {
        // Existing AOT modules may resolve these original metadata tokens.
        // PreserveAll retains their RIDs; verify their resolved meanings too.
        var map=new Dictionary<uint,string>();
        var tables=new [] {m.TablesStream.TypeRefTable,m.TablesStream.MemberRefTable,
            m.TablesStream.TypeSpecTable,m.TablesStream.MethodSpecTable,
            m.TablesStream.StandAloneSigTable,m.TablesStream.ParamTable,
            m.TablesStream.EventTable,m.TablesStream.PropertyTable};
        foreach(var table in tables) for(uint row=1;row<=table.Rows;row++) {
            uint token=new MDToken(table.Table,row).Raw;
            var value=m.ResolveToken(token);
            Require(value!=null,"Original metadata token does not resolve: "+token.ToString("x8"));
            map.Add(token,value.ToString());
        }
        return map;
    }
    public static int Main(string[] args) {
        try {
            if(args.Length==3 && args[0]=="--generate-baseline-profile") {
                ManagedProfile.GenerateBaseline(args[1],args[2]); Console.WriteLine("Profile created: "+args[2]); return 0;
            }
            string profilePath=Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"..","profiles","apk-managed-v1.json"));
            var arguments=new List<string>(args);
            int profileOption=arguments.IndexOf("--profile");
            if(profileOption>=0) {
                Require(profileOption+1<arguments.Count,"--profile requires a path");
                profilePath=Path.GetFullPath(arguments[profileOption+1]); arguments.RemoveRange(profileOption,2);
            }
            bool probe=arguments.Count==2 && arguments[0]=="--probe";
            Require(probe || arguments.Count==5,"Usage: PatchManaged.exe INPUT_DLL_DIR OUTPUT_DLL_DIR ACCOUNT SIGNATURE_BASE64 CERTIFICATE_DER [--profile JSON], or --probe INPUT_DLL_DIR [--profile JSON]");
            string input=Path.GetFullPath(arguments[probe ? 1 : 0]);
            var context=ModuleDef.CreateModuleContext();
            var resolver=(AssemblyResolver)context.AssemblyResolver;
            resolver.UseGAC=false; resolver.PreSearchPaths.Add(input);
            var matches=ManagedProfile.Validate(input,profilePath,context);
            try {
            if(probe) {
                Console.WriteLine(new JavaScriptSerializer().Serialize(new {compatible=true,profile_id=matches.Profile.profile_id,
                    normalization=ManagedProfile.Normalization,source_sha256=matches.InputHashes,methods=matches.Findings}));
                return 0;
            }
            string output=Path.GetFullPath(arguments[1]),account=arguments[2],signature=arguments[3];
            Require(input!=output,"Input and output must differ");
            Require(Regex.IsMatch(account,"^[a-z0-9][a-z0-9._-]{2,29}$"),"Account must be 3-30 lowercase ASCII account characters");
            Require(Convert.FromBase64String(signature).Length==256,"Expected RSA-2048 signature");
            byte[] der=File.ReadAllBytes(arguments[4]);
            int javaHash=1; foreach(byte b in der) javaHash=unchecked(31*javaHash+(sbyte)b);
            int certHash; using(var cert=new X509Certificate2(der)) certHash=cert.GetHashCode();
            foreach(var name in ModifiedNames) {
                Require(!File.Exists(Path.Combine(output,name)),"Output already exists: "+name);
            }
            Require(!File.Exists(Path.Combine(output,"managed-patch-report.json")),"Output report already exists");
            // All profile signatures, complete method bodies, dependency patterns,
            // and both in-memory edits must succeed before creating any outputs.
            PatchAndroid(matches.Modules[ModifiedNames[0]],account,Hex(der),matches);
            PatchMobile(matches.Modules[ModifiedNames[1]],signature,javaHash,certHash,matches);
            Directory.CreateDirectory(output);
            var outputs=new List<object>();
            foreach(var name in ModifiedNames) {
                string dest=Path.Combine(output,name);
                var m=matches.Modules[name];
                    var tokens=Tokens(m); var references=References(m); var mvid=m.Mvid;
                    var options=new ModuleWriterOptions(m);
                    options.MetadataOptions.Flags=MetadataFlags.PreserveAll;
                    m.Write(dest,options);
                    using(var verify=ModuleDefMD.Load(dest)) {
                        Require(verify.Mvid==mvid,"MVID changed; Android native typemaps could break");
                        var after=Tokens(verify);
                        Require(tokens.Count==after.Count && tokens.All(k=>after.ContainsKey(k.Key)&&after[k.Key]==k.Value),
                            "Metadata definition tokens changed; native typemaps could break");
                        foreach(var reference in references) {
                            var resolved=verify.ResolveToken(reference.Key);
                            Require(resolved!=null && resolved.ToString()==reference.Value,
                                "Original reference token changed: "+reference.Key.ToString("x8"));
                        }
                        foreach(var method in Methods(verify).Where(x=>x.HasBody)) {
                            var body=method.Body;
                            foreach(var ins in body.Instructions) {
                                var branch=ins.Operand as Instruction;
                                Require(branch==null || body.Instructions.Contains(branch),"Invalid branch target");
                            }
                        }
                    }
                    outputs.Add(new {file=name,original_sha256=matches.InputHashes[name],patched_sha256=Hash(dest),mvid=mvid.ToString(),
                        definition_tokens_preserved=true,reference_tokens_preserved=true,
                        checked_definition_tokens=tokens.Count,checked_reference_tokens=references.Count});
            }
            var report=new {version=matches.Modules[ModifiedNames[0]].Assembly.Version.ToString(),account=account,java_signature_hash=javaHash,x509_certificate_hash=certHash,
                profile_id=matches.Profile.profile_id,profile_sha256=Hash(profilePath),normalization=ManagedProfile.Normalization,
                compatibility_methods=matches.Findings,source_sha256=matches.InputHashes,
                certificate_sha256=Hex(SHA256.Create().ComputeHash(der)),changes=Changes,outputs=outputs,runtime_verified=false};
            string json=new JavaScriptSerializer().Serialize(report);
            File.WriteAllText(Path.Combine(output,"managed-patch-report.json"),json);
            Console.WriteLine(json); return 0;
            } finally {matches.Dispose();}
        } catch(Exception ex) { Console.Error.WriteLine(ex.ToString()); return 1; }
    }
}
