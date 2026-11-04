// Test-only metadata rewrites. Outputs are never executed or installed.
using System;
using System.IO;
using System.Linq;
using dnlib.DotNet;
using dnlib.DotNet.Emit;
using dnlib.DotNet.Writer;

public static class StreamerFixture {
    public static void Create(string input,string output,string mode) {
        if(File.Exists(output))throw new IOException("Fixture output must be new");
        using(var m=ModuleDefMD.Load(input)){
            foreach(var method in m.GetTypes().SelectMany(t=>t.Methods).Where(x=>x.HasBody))method.Body.KeepOldMaxStack=true;
            if(mode=="layout"){
                var type=new TypeDefUser("LocalProfileTest","UnrelatedMarker",m.CorLibTypes.Object.TypeDefOrRef);
                var method=new MethodDefUser("UnrelatedNoop",MethodSig.CreateStatic(m.CorLibTypes.Void),MethodImplAttributes.IL|MethodImplAttributes.Managed,MethodAttributes.Public|MethodAttributes.Static);
                method.Body=new CilBody();method.Body.Instructions.Add(Instruction.Create(OpCodes.Ret));
                type.Methods.Add(method);m.Types.Insert(1,type);
            }else if(mode=="encoding"){
                foreach(var method in m.GetTypes().SelectMany(t=>t.Methods).Where(x=>x.HasBody))method.Body.SimplifyMacros(method.Parameters);
            }else if(mode=="ambiguous"){
                var type=m.GetTypes().Single(x=>x.FullName=="VirtualDesktop.Streamer.UpdateHelper");
                var original=type.Methods.Single(x=>x.MDToken.Raw==0x0600051f);
                var method=new MethodDefUser(original.Name,original.MethodSig,original.ImplAttributes,original.Attributes);
                method.Body=new CilBody();method.Body.Instructions.Add(Instruction.Create(OpCodes.Ldnull));method.Body.Instructions.Add(Instruction.Create(OpCodes.Ret));
                type.Methods.Add(method);
            }else throw new ArgumentException("Unknown fixture mode");
            var options=new ModuleWriterOptions(m);
            if(mode!="layout")options.MetadataOptions.Flags=MetadataFlags.PreserveAll;
            m.Write(output,options);
        }
    }
}
